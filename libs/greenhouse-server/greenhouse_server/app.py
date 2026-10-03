"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import AuthConfig, FastApiMCP
from sqlalchemy.engine import Engine

from greenhouse_core.database import create_db_engine, create_session_factory, init_db
from greenhouse_core.devices import DeviceGateway, build_default_registry
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import set_display_timezone
from greenhouse_server.auth import bootstrap_admin, require_user
from greenhouse_server.config import Settings
from greenhouse_server.routes import (
    activity,
    alerts,
    bulk,
    charts,
    clusters,
    configs,
    decisions,
    efficacy,
    forecast,
    health,
    insights,
    irrigators,
    operations,
    plants,
    preferences,
    quality,
    scheduler,
    search,
    sensors,
    vacation,
    well_known,
    windows,
)
from greenhouse_server.routes import (
    auth as auth_routes,
)
from greenhouse_server.scheduler import (
    apply_persisted_pause,
    init_health_monitor,
    init_scheduler,
    start_scheduler,
    stop_scheduler,
)
from greenhouse_server.services.irrigation import rearm_leak_checks
from greenhouse_server.services.jobs import read_session
from greenhouse_server.services.notify import NtfyClient
from greenhouse_server.services.weather import WeatherClient
from greenhouse_server.state import get_settings
from greenhouse_server.web.exception_handlers import register_web_exception_handlers
from greenhouse_server.web.router import web_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from fastapi import APIRouter


def _init_tuya(app: FastAPI) -> None:
    """Build the one shared Tuya gateway and the registry that wraps it.

    Stores ``app.state.device_gateway`` (a single ``DeviceGateway`` — one Cloud
    client, one token) and ``app.state.device_registry`` (adapters bound to it).
    Both are set to ``None`` when credentials are missing or construction fails,
    so the server still starts in degraded mode for tests and credential-less
    environments.
    """
    try:
        gateway = DeviceGateway()
    except Exception:  # noqa: BLE001
        app.state.device_gateway = None
        app.state.device_registry = None
        return
    app.state.device_gateway = gateway
    app.state.device_registry = build_default_registry(gateway)


def _init_ntfy_notifier(settings: Settings) -> NtfyClient | None:
    """Build the ntfy client from settings, or ``None`` when unconfigured.

    Enabled only when both ``ntfy_server_url`` and ``ntfy_topic`` are set
    (mirrors the fail-closed pattern of the MCP token and device registry).
    """
    if not settings.ntfy_server_url or not settings.ntfy_topic:
        return None
    return NtfyClient(settings.ntfy_server_url, settings.ntfy_topic, settings.ntfy_token)


_mcp_bearer = HTTPBearer(auto_error=False)


def require_mcp_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_mcp_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate `/mcp` behind a static bearer token.

    Fail-closed: when `settings.mcp_token` is unset the endpoint returns 503,
    so a misconfigured deployment never silently leaves MCP open. With the
    token configured, missing or wrong `Authorization: Bearer <token>` yields
    401. The token has full reach over every `/api/v1` route exposed as an MCP
    tool — including irrigation actuation — so it MUST be a high-entropy
    secret (generate with `openssl rand -hex 32`).
    """
    if settings.mcp_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP auth not configured",
        )
    if creds is None or creds.credentials != settings.mcp_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MCP token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# The 14 OpenAPI tags, in documentation order.
_OPENAPI_TAGS: tuple[dict[str, str], ...] = (
    {"name": "clusters", "description": "Manage plant clusters (groups irrigated together)"},
    {"name": "plants", "description": "Manage plants within clusters"},
    {"name": "irrigators", "description": "Manage and control irrigation devices"},
    {"name": "sensors", "description": "Manage sensor devices"},
    {"name": "configs", "description": "Irrigation configuration per cluster"},
    {"name": "operations", "description": "Smart irrigation, monitoring, sync, stats, and analytics"},
    {"name": "scheduler", "description": "Background job management and health checks"},
    {"name": "alerts", "description": "Alert inbox with deduplication and ack/resolve lifecycle"},
    {"name": "activity", "description": "Cross-cutting activity timeline"},
    {"name": "decisions", "description": "Irrigation decision audit log"},
    {"name": "preferences", "description": "User preferences (units, timezone, theme, dry-run flag)"},
    {"name": "vacation", "description": "Vacation windows — pause irrigation while away"},
    {"name": "search", "description": "Global search across clusters, plants, sensors, and irrigators"},
    {"name": "bulk", "description": "Bulk operations — emergency stop all irrigators"},
)


# Every /api/v1 router gated by `require_user`, in include order. That order is the OpenAPI
# path order and therefore the MCP tool order — append, never reorder.
def _protected_api_routers() -> "tuple[APIRouter, ...]":
    """Return the auth-gated API routers in registration order (= OpenAPI path order).

    Read at ``create_app`` time, so a rebound ``<module>.router`` is picked up.
    """
    return (
        clusters.router,
        plants.router,
        irrigators.router,
        sensors.router,
        configs.router,
        operations.router,
        scheduler.router,
        charts.router,
        alerts.router,
        activity.router,
        decisions.router,
        forecast.router,
        preferences.router,
        vacation.router,
        search.router,
        bulk.router,
        insights.router,
        health.router,
        quality.router,
        efficacy.router,
        windows.router,
    )


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    if engine is None:
        engine = create_db_engine(settings.db_url)
    init_db(engine)

    app = _new_fastapi(_make_lifespan(settings))
    tz_name = _init_state(app, settings, engine)
    _init_background(app, settings, tz_name)

    # Bootstrap the admin user from env vars before serving requests, so the
    # operator never sees a working API that rejects every call with 401.
    bootstrap_admin(engine, settings)

    _include_api_routers(app)
    _mount_web(app)
    _mount_mcp(app)  # must stay last: FastApiMCP snapshots the routes included above
    return app


def _make_lifespan(settings: Settings) -> "Callable[[FastAPI], AbstractAsyncContextManager[None]]":
    """Build the lifespan: start the scheduler (and re-arm leak checks) on startup, stop it on shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> "AsyncIterator[None]":  # noqa: ARG001 — FastAPI lifespan protocol
        """Start the scheduler and re-arm leak checks on startup; stop the scheduler on shutdown."""
        if settings.enable_scheduler:
            start_scheduler()
            # Leak-check jobs are in-memory: restore any a restart dropped.
            rearm_leak_checks()
        yield
        if settings.enable_scheduler:
            stop_scheduler()

    return lifespan


def _new_fastapi(lifespan: "Callable[[FastAPI], AbstractAsyncContextManager[None]]") -> FastAPI:
    """The bare FastAPI app: title, tags and route-name operation ids (the MCP tool names)."""
    return FastAPI(
        title="Greenhouse API",
        description=(
            "Smart plant irrigation system with evidence-based plant care, "
            "multi-sensor conflict resolution, and self-learning irrigation profiles.\n\n"
        ),
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=[dict(tag) for tag in _OPENAPI_TAGS],
        generate_unique_id_function=lambda route: route.name,
    )


def _init_state(app: FastAPI, settings: Settings, engine: Engine) -> str:
    """Store the shared dependencies on app.state (read by deps.py); return the startup timezone."""
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    _init_tuya(app)

    # UserPreferences.timezone is the single authoritative clock: the engine
    # gates windows against it, so the scheduler's wall-clock cron jobs, the
    # weather forecast localization, and the display formatter must all read it
    # too. Resolve it once here and thread it into every consumer.
    tz_name = _startup_timezone(app)
    set_display_timezone(tz_name)

    app.state.weather_client = WeatherClient(
        lat=settings.weather_lat,
        lon=settings.weather_lon,
        tz=tz_name,
    )
    app.state.ntfy_notifier = _init_ntfy_notifier(settings)
    app.state.plant_db = _init_plant_db(settings)
    return tz_name


def _init_background(app: FastAPI, settings: Settings, tz_name: str) -> None:
    """Register the scheduler jobs and the device-health monitor, then restore a persisted pause."""
    init_scheduler(app, settings, tz_name=tz_name)
    init_health_monitor(app, settings)
    _restore_persisted_scheduler_pause(app)


def _include_api_routers(app: FastAPI) -> None:
    """Mount the JSON API: /auth/login open, every other /api/v1 router behind `require_user`."""
    # /auth/login is the only unauthenticated /api/v1 entry point. Everything
    # else is gated by `require_user` via include_router(..., dependencies=).
    prefix = "/api/v1"
    app.include_router(auth_routes.router, prefix=prefix)
    protected = [Depends(require_user)]
    for router in _protected_api_routers():
        app.include_router(router, prefix=prefix, dependencies=protected)

    # OAuth discovery stubs at root (not /api/v1) so MCP HTTP clients that
    # probe RFC 9728 / RFC 8414 before applying the bearer header don't crash
    # on FastAPI's default 404 body. See routes/well_known.py for the
    # upstream Claude Code regression this works around.
    app.include_router(well_known.router)


def _mount_web(app: FastAPI) -> None:
    """Mount the web frontend: static files, the HTML routes and the HTML/JSON error handlers."""
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)
    register_web_exception_handlers(app)


def _mount_mcp(app: FastAPI) -> None:
    """Expose every JSON API endpoint as an MCP tool at /mcp, behind `require_mcp_token`.

    Streamable HTTP. Web routes are auto-excluded because they set include_in_schema=False.
    Bearer-token auth gates the mount: with no GREENHOUSE_MCP_TOKEN configured the endpoint
    fails closed with 503; with a token configured, MCP clients must send
    `Authorization: Bearer <token>`. The live instance is kept on ``app.state.mcp``.
    """
    mcp = FastApiMCP(
        app,
        name="greenhouse",
        description=(
            "Smart plant irrigation system — manage clusters, plants, sensors, "
            "irrigators, configs; run smart-irrigation decisions; read history, "
            "stats, and learning reports."
        ),
        auth_config=AuthConfig(dependencies=[Depends(require_mcp_token)]),
    )
    mcp.mount_http()
    app.state.mcp = mcp


def _startup_timezone(app: FastAPI) -> str:
    """Read ``UserPreferences.timezone`` at startup, defaulting to UTC.

    The preferences row is created on first access, so this also seeds the
    singleton. Falls back to ``"UTC"`` (matching the column default) if the
    DB cannot be read yet.
    """
    try:
        with read_session(app) as session:
            tz = IrrigationRepository(session).get_preferences().timezone
            session.commit()
            return tz or "UTC"
    except Exception:  # noqa: BLE001
        return "UTC"


def _restore_persisted_scheduler_pause(app: FastAPI) -> None:
    """If `user_preferences.scheduler_paused` is True, re-pause check_all.

    Runs once on startup after `init_scheduler` so a pause survives a
    container restart. Silently skipped if preferences cannot be read.
    """
    try:
        with read_session(app) as session:
            paused = IrrigationRepository(session).get_preferences().scheduler_paused
            session.commit()
        apply_persisted_pause(paused)
    except Exception:  # noqa: BLE001, S110
        pass


def _init_plant_db(settings: Settings) -> PlantDatabase:
    """Initialize plant database from settings or default."""
    if settings.plant_db_path:
        return PlantDatabase(db_path=Path(settings.plant_db_path))
    return PlantDatabase()


def main() -> None:
    """Entry point for greenhouse-server command."""
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
