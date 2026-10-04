"""Characterization: the HTMX/Jinja2 web UI renders exactly what it renders today.

Pins, for one deterministic seeded greenhouse (frozen clock, offline weather, clean env, ``TZ=UTC``):

* the web route inventory in registration order → ``golden/web/routes.json``;
* every GET page / HX fragment: status, relevant headers and the full body →
  ``golden/web/get/<case>.html`` (one file per route + variant);
* the template name and sorted context keys each GET renders →
  ``golden/web/template_context.json``.

Mutating routes live in ``test_contract_web_mutations.py``; error pages / auth redirects in
``test_contract_web_errors.py``. Both reuse the seed and helpers defined here.

Only the package version is normalized (``<VERSION>``) — release-please bumps it. Nothing
else is: time is frozen at ``FROZEN_INSTANT`` instead.

App construction mirrors ``tests/server/conftest.py::_make_stubbed_app`` exactly (same
settings, the same fake device wiring and overrides), except that each app is built on a
*copy* of an already migrated + seeded in-memory database. The copy is byte-for-byte what a
fresh ``_make_stubbed_app`` + seed produces (it is made from one, inside the frozen clock),
and skipping the re-run of the migrations / admin password hash makes a fresh app per case
cheap (~20 ms instead of ~0.8 s).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import _iter_routes_with_context
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fake_data import FAKE_DEVICE_ID, FAKE_DEVICE_ID_2, FAKE_DEVICE_IP, FAKE_LOCAL_KEY, FAKE_SENSOR_ID
from golden import FROZEN_TS, assert_golden, assert_golden_json, install_offline_weather
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.app import create_app
from greenhouse_server.auth import AuthenticatedUser, require_user, require_web_user
from greenhouse_server.config import Settings
from greenhouse_server.deps import get_device_gateway, get_device_registry
from greenhouse_server.web.context import APP_VERSION
from greenhouse_server.web.router import web_router
from greenhouse_server.web.templating import templates
from server.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_ADMIN_USERNAME,
    TEST_AUTH_SECRET,
    FakeDeviceWiring,
    _make_stubbed_app,
)

H = 3600
T = FROZEN_TS  # 2026-04-15T10:00:00Z, a Wednesday

# ── Seeded ids (autoincrement from an empty DB, so they are stable) ──────────────────────────
INDOOR, OUTDOOR, EMPTY = 1, 2, 3
MONSTERA, ALOCASIA, LOQUAT = 1, 2, 3  # plant ids; ALOCASIA is critically dry
S_MONSTERA, S_ALOCASIA, S_LOQUAT, S_AMBIENT = 1, 2, 3, 4
IRR_INDOOR, IRR_OUTDOOR = 1, 2
ALERT_OPEN, ALERT_ACKED, ALERT_RESOLVED = 1, 2, 3
WINDOW = 1
VACATION_UPCOMING, VACATION_PAST = 1, 2


# ── Seed ──────────────────────────────────────────────────────────────────────────────────────


def seed_greenhouse(repo: IrrigationRepository) -> None:
    """Populate a deterministic greenhouse. Every timestamp is derived from ``T``.

    Must run inside the frozen clock: repository defaults (``created_at`` …) read ``time.time()``.
    """
    indoor = repo.add_cluster("Indoor Jungle", location="Living room", environment="indoor")
    outdoor = repo.add_cluster("Balcony Orchard", location="Balcony", environment="outdoor")
    repo.add_cluster("Empty Shelf", environment="indoor")

    monstera = repo.add_plant(
        indoor,
        "Monstera deliciosa",
        category="tropical",
        water_needs="medium",
        light_needs="medium",
        ideal_temp_min=18.0,
        ideal_temp_max=27.0,
        ideal_humidity_min=60.0,
        ideal_humidity_max=80.0,
        notes="By the window",
    )
    alocasia = repo.add_plant(
        indoor,
        "Alocasia amazonica",
        category="tropical",
        water_needs="high",
        ideal_temp_min=18.0,
        ideal_temp_max=29.0,
        ideal_humidity_min=60.0,
        ideal_humidity_max=85.0,
    )
    loquat = repo.add_plant(outdoor, "Eriobotrya japonica", category="fruit_tree", water_needs="medium")

    irr_indoor = repo.add_irrigator(
        indoor,
        FAKE_DEVICE_ID,
        "Jungle Pump",
        "rainpoint.ik10pw",
        {"device_ip": FAKE_DEVICE_IP, "local_key": FAKE_LOCAL_KEY},
    )
    repo.update_irrigator(irr_indoor, reservoir_l=10.0, flow_rate_l_per_min=0.5)
    irr_outdoor = repo.add_irrigator(outdoor, FAKE_DEVICE_ID_2, "Balcony Valve", "rainpoint.ik10pw", {})

    start = T - 72 * H
    s_monstera = repo.add_sensor(
        indoor, FAKE_SENSOR_ID, "Monstera Probe", "tuya.tr301z", {}, plant_id=monstera, assignment_started_at=start
    )
    s_alocasia = repo.add_sensor(
        indoor,
        "fake_tuya_sensor_00000002",
        "Alocasia Probe",
        "tuya.tr301z",
        {},
        plant_id=alocasia,
        assignment_started_at=start,
    )
    s_loquat = repo.add_sensor(
        outdoor,
        "fake_tuya_sensor_00000003",
        "Loquat Probe",
        "tuya.tr301z",
        {},
        plant_id=loquat,
        assignment_started_at=start,
    )
    s_ambient = repo.add_sensor(indoor, "fake_tuya_sensor_00000004", "Room Climate", "tuya.tr301z", {})

    # Readings every 2 h over the last 48 h; newest 30 min ago (fresh: < SENSOR_READING_STALE_SECONDS).
    for k in range(25):
        ts = T - 1800 - k * 2 * H
        monstera_soil = 97.0 if k == 5 else round(50.0 + k * 0.5, 1)  # k == 5: a Hampel spike
        repo.add_sensor_reading(
            s_monstera,
            timestamp=ts,
            soil_moisture=monstera_soil,
            temperature=round(22.0 + (k % 3) * 0.5, 1),
            env_humidity=float(60 + k % 4),
            light=1200 + 10 * k,
            battery_state="high",
        )
        repo.add_sensor_reading(
            s_alocasia,
            timestamp=ts,
            soil_moisture=round(8.0 + k * 0.4, 1),
            temperature=round(23.0 + (k % 2) * 0.5, 1),
            env_humidity=float(55 + k % 3),
            light=800 + 5 * k,
            battery_state="low" if k == 0 else "middle",
        )
        repo.add_sensor_reading(
            s_loquat,
            timestamp=ts,
            soil_moisture=round(35.0 + k * 0.3, 1),
            temperature=round(16.0 + (k % 5), 1),
            env_humidity=float(70 - k % 6),
            light=20000 - 100 * k,
            battery_state="high",
        )
        if k % 4 == 0:
            repo.add_sensor_reading(
                s_ambient, timestamp=ts, temperature=round(21.0 + k * 0.1, 1), env_humidity=float(50 + k % 5)
            )

    # Irrigation history.
    repo.add_irrigation_event(irr_indoor, "start", "auto", duration_minutes=3, notes="Scheduled", timestamp=T - 30 * H)
    repo.add_irrigation_event(irr_indoor, "off", "auto", timestamp=T - 30 * H + 180)
    repo.add_irrigation_event(
        irr_indoor, "start", "manual", duration_minutes=2, notes="Manual start via web UI (2 min)", timestamp=T - 8 * H
    )
    repo.add_irrigation_event(irr_indoor, "off", "manual", notes="Manual stop via web UI", timestamp=T - 8 * H + 120)
    repo.add_irrigation_event(irr_outdoor, "start", "auto", duration_minutes=5, timestamp=T - 20 * H)
    repo.add_irrigation_event(irr_outdoor, "schedule_updated", "auto", notes="interval 24h", timestamp=T - 20 * H + 5)

    # Decision log (newest first in the UI).
    def _payload(cluster_id: int, evaluated_at: int, action: str, minutes: int, reasons: list[dict]) -> dict:
        return {
            "cluster_id": cluster_id,
            "evaluated_at": evaluated_at,
            "action": action,
            "duration_minutes": minutes,
            "interval_hours": 12,
            "confidence": 0.8,
            "reasons": reasons,
        }

    dry = {"code": "sensor_very_dry", "message": "Alocasia amazonica soil at 8%", "severity": "critical"}
    hot = {"code": "temp_high", "message": "Warm room", "severity": "warning", "duration_delta": 1}
    ok = {"code": "sensor_adequate", "message": "Soil moisture adequate", "severity": "info"}
    cool = {"code": "cooldown", "message": "Irrigated 1h ago", "severity": "info"}
    for cluster_id, at, action, minutes, reasons, actuated in (
        (indoor, T - 30 * H, "irrigate", 3, [dry, hot], True),
        (indoor, T - 8 * H, "irrigate", 2, [dry], True),
        (indoor, T - 7 * H, "skip", 0, [cool], False),
        (outdoor, T - 20 * H, "irrigate", 5, [ok], True),
    ):
        repo.add_decision_log(
            cluster_id=cluster_id,
            evaluated_at=at,
            action=action,
            duration_minutes=minutes,
            interval_hours=12,
            confidence=0.8,
            primary_code=reasons[0]["code"],
            reason_text="; ".join(r["message"] for r in reasons),
            payload=_payload(cluster_id, at, action, minutes, reasons),
            triggered_by="auto",
            actuated=actuated,
        )

    # Alerts: one open, one acknowledged, one resolved.
    repo.upsert_alert(
        f"soil_dry:plant:{alocasia}",
        "health",
        "soil_dry",
        "Alocasia is very dry",
        "Soil moisture 8% is below the critical threshold.",
        severity="critical",
        entity_type="plant",
        entity_id=alocasia,
        cluster_id=indoor,
        plant_id=alocasia,
        seen_at=T - 2 * H,
    )
    acked = repo.upsert_alert(
        f"battery_low:sensor:{s_alocasia}",
        "health",
        "battery_low",
        "Alocasia Probe battery low",
        "Replace the batteries soon.",
        severity="warning",
        entity_type="sensor",
        entity_id=s_alocasia,
        cluster_id=indoor,
        seen_at=T - 5 * H,
    )
    acked.status, acked.acknowledged_at = "acknowledged", T - 4 * H
    resolved = repo.upsert_alert(
        f"check_failed:cluster:{outdoor}",
        "scheduler",
        "check_failed",
        "Check failed for Balcony Orchard",
        "Transient error during the scheduled check.",
        severity="warning",
        entity_type="cluster",
        entity_id=outdoor,
        cluster_id=outdoor,
        seen_at=T - 26 * H,
    )
    resolved.status, resolved.resolved_at = "resolved", T - 25 * H

    # Activity timeline.
    for at, source, entity_type, entity_id, severity, code, message in (
        (T - 30 * H, "scheduler", "cluster", indoor, "info", "irrigation_started", "Auto irrigation 3 min"),
        (T - 26 * H, "scheduler", "cluster", outdoor, "warning", "check_failed", "Scheduled check failed"),
        (T - 8 * H, "web", "irrigator", irr_indoor, "info", "manual_start", "Manual start via web UI"),
        (T - 2 * H, "health", "plant", alocasia, "critical", "soil_dry", "Alocasia is very dry"),
    ):
        repo.add_activity_event(
            source, entity_type, code, message, entity_id=entity_id, severity=severity, timestamp=at, payload={"k": 1}
        )

    # Daily plant-health snapshots (Monstera) for the hero ring / timeline.
    for days_ago, score in ((3, 72.5), (2, 80.0), (1, 77.25)):
        ts = T - days_ago * 24 * H
        date_key = f"2026-04-{15 - days_ago:02d}"
        repo.upsert_plant_health(
            monstera,
            date_key,
            score,
            soil_in_band_pct=80.0,
            temp_in_band_pct=90.0,
            humidity_in_band_pct=70.0,
            efficiency=0.6,
            sample_count=12,
            timestamp=ts,
        )

    # Watering window on the outdoor cluster: 06–09, Mon/Wed/Fri.
    repo.add_irrigation_window(outdoor, start_hour=6, end_hour=9, weekday_mask=1 | 4 | 16, label="Morning")

    # Vacations: one upcoming (drives the budget readout), one past.
    repo.add_vacation_window(T + 5 * 24 * H, T + 12 * 24 * H, contact_email="neighbour@example.com", notes="Spain")
    repo.add_vacation_window(T - 40 * 24 * H, T - 33 * 24 * H, notes="Ski week")

    # Configs: per-cluster override on the indoor cluster, global caps.
    repo.set_irrigation_config(
        cluster_id=indoor, mode="smart", duration_minutes=3, interval_hours=12, auto_run=True, max_events_per_day=4
    )
    repo.set_irrigation_config(cluster_id=outdoor, duration_minutes=5, auto_run=False)
    repo.update_global_irrigation_config(daily_cap_minutes=30, max_events_per_day=6)

    # Preferences.
    repo.update_preferences(theme="dark", refresh_interval_seconds=60, default_cluster_id=indoor, notify_auto=False)


# ── App factory (copy of a seeded template DB per app) ────────────────────────────────────────

_TEMPLATE_DB: sqlite3.Connection | None = None


def _seeded_template() -> sqlite3.Connection:
    """Build (once per process) a migrated + seeded DB through the real ``_make_stubbed_app``.

    The first caller is always inside ``frozen_clock`` + ``clean_env`` (see ``web_env``), so
    the result is identical whichever test builds it.
    """
    global _TEMPLATE_DB
    if _TEMPLATE_DB is None:
        app, engine = _make_stubbed_app(bypass_auth=True)
        session = app.state.session_factory()
        try:
            seed_greenhouse(IrrigationRepository(session))
            session.commit()
        finally:
            session.close()
        raw = engine.raw_connection()
        try:
            snapshot = sqlite3.connect(":memory:", check_same_thread=False)
            raw.driver_connection.backup(snapshot)
        finally:
            raw.close()
        engine.dispose()
        _TEMPLATE_DB = snapshot
    return _TEMPLATE_DB


def build_app(*, bypass_auth: bool = True, seeded: bool = True, **settings_override):
    """A fresh stubbed app (same wiring as ``_make_stubbed_app``) on a copy of the seeded DB."""
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if seeded:
        raw = engine.raw_connection()
        try:
            _seeded_template().backup(raw.driver_connection)
        finally:
            raw.close()
    base = {
        "db_url": "sqlite://",
        "enable_scheduler": False,
        "auth_enabled": True,
        "auth_secret_key": TEST_AUTH_SECRET,
        "auth_admin_username": TEST_ADMIN_USERNAME,
        "auth_admin_password": TEST_ADMIN_PASSWORD,
    }
    base.update(settings_override)
    application = create_app(Settings(**base), engine=engine)

    wiring = FakeDeviceWiring()
    application.state.fake_devices = wiring
    application.dependency_overrides[get_device_registry] = lambda: wiring.registry
    application.dependency_overrides[get_device_gateway] = lambda: None
    if bypass_auth:
        synthetic = AuthenticatedUser(id=1, username=TEST_ADMIN_USERNAME)
        application.dependency_overrides[require_user] = lambda: synthetic
        application.dependency_overrides[require_web_user] = lambda: synthetic
    install_offline_weather(application)
    return application, engine


@pytest.fixture
def web_env(clean_env, frozen_clock):
    """Hermetic + frozen environment; builds the shared seeded template inside it."""
    _seeded_template()
    return frozen_clock


@pytest.fixture
def make_client(web_env):
    """Factory: a *fresh* seeded app + TestClient per call (used by every mutating case)."""
    engines = []

    def _make(**kwargs) -> tuple[Any, TestClient]:
        application, engine = build_app(**kwargs)
        engines.append(engine)
        return application, TestClient(application, raise_server_exceptions=False)

    yield _make
    for engine in engines:
        engine.dispose()


_SHARED: dict[str, Any] = {}


def reset_shared_app() -> Any:
    """The per-process read-only app, reset to the freshly-seeded state.

    Building an app costs ~0.5 s (``FastApiMCP`` renders the OpenAPI schema), so the GET sweep
    reuses one app and resets everything a request can observe: the DB is restored from the
    seeded template, the fake device wiring is replaced, and the two process-global bits
    ``create_app`` sets (scheduler job registration, display timezone) are re-applied exactly as
    ``create_app`` applies them for this seed (prefs timezone ``UTC``, not paused).
    """
    from greenhouse_core.utils import set_display_timezone
    from greenhouse_server.scheduler import init_scheduler

    if "app" not in _SHARED:
        _SHARED["app"], _SHARED["engine"] = build_app()
    application, engine = _SHARED["app"], _SHARED["engine"]
    raw = engine.raw_connection()
    try:
        _seeded_template().backup(raw.driver_connection)
    finally:
        raw.close()
    wiring = FakeDeviceWiring()
    application.state.fake_devices = wiring
    application.dependency_overrides[get_device_registry] = lambda: wiring.registry
    init_scheduler(application, application.state.settings, tz_name="UTC")
    set_display_timezone("UTC")
    return application


@pytest.fixture
def seeded_get_client(web_env) -> TestClient:
    """TestClient on the shared app, freshly reset to the seeded state."""
    return TestClient(reset_shared_app(), raise_server_exceptions=False)


# ── Response rendering helpers ────────────────────────────────────────────────────────────────

_PINNED_HEADERS = ("content-type", "location", "content-disposition", "set-cookie", "www-authenticate")


def normalize(textual: str) -> str:
    """Replace the release-please-managed package version (the only normalization)."""
    return textual.replace(APP_VERSION, "<VERSION>")


_JWT_RE = re.compile(r"eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+")


def _digest_jwts(value: str) -> str:
    """Replace each JWT with its sha256 — still byte-exact, but no token-shaped text in goldens (gitleaks)."""
    return _JWT_RE.sub(lambda m: f"<JWT sha256={hashlib.sha256(m.group().encode()).hexdigest()}>", value)


def pinned_headers(resp) -> dict[str, str]:
    """Headers that are part of the contract: content type, redirects, cookies, every ``HX-*``."""
    out = {name: resp.headers[name] for name in _PINNED_HEADERS if name in resp.headers}
    if "set-cookie" in out:
        out["set-cookie"] = _digest_jwts(out["set-cookie"])
    for name, value in resp.headers.items():
        if name.lower().startswith("hx-"):
            out[name.lower()] = value
    return dict(sorted(out.items()))


def render_response(method: str, url: str, request_headers: dict[str, str], resp) -> str:
    """Golden text: request line, status, pinned headers, blank line, full body."""
    lines = [f"<!-- {method} {url} -->"]
    for name, value in sorted(request_headers.items()):
        lines.append(f"<!-- request {name}: {value} -->")
    lines.append(f"<!-- status: {resp.status_code} -->")
    for name, value in pinned_headers(resp).items():
        lines.append(f"<!-- {name}: {value} -->")
    body = resp.text
    if "\r" in body:
        # Golden files are read with universal newlines; keep CR visible instead of losing it.
        lines.append("<!-- body: carriage returns shown as <CR> -->")
        body = body.replace("\r", "<CR>")
    return normalize("\n".join(lines) + "\n\n" + body + ("" if body.endswith("\n") else "\n"))


@dataclass
class TemplateSpy:
    """Records ``(template name, sorted context keys)`` for every ``TemplateResponse`` call."""

    calls: list[list] = field(default_factory=list)

    def install(self, monkeypatch) -> None:
        original = templates.TemplateResponse

        def spy(*args, **kwargs):
            # Signature: TemplateResponse(request, name, context=None, ...) — positional or keyword.
            name = kwargs.get("name", args[1] if len(args) > 1 else None)
            context = kwargs.get("context", args[2] if len(args) > 2 else None) or {}
            self.calls.append([name, sorted(context)])
            return original(*args, **kwargs)

        monkeypatch.setattr(templates, "TemplateResponse", spy)


# ── 1. Route inventory ────────────────────────────────────────────────────────────────────────


def web_routes(routes) -> list:
    """Flatten (FastAPI >= 0.140 keeps included routers lazy) and keep the web-UI routes, in order."""
    out = []
    for original, context in _iter_routes_with_context(routes):
        route = context if context is not None else original
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None and endpoint.__module__.startswith("greenhouse_server.web.routes."):
            out.append(route)
    return out


def _route_inventory(application) -> list[dict]:
    """Every route the web router contributes to ``application``, in registration order."""
    return [
        {
            "methods": sorted(route.methods),
            "path": route.path,
            "name": route.name,
            "endpoint": f"{route.endpoint.__module__}.{route.endpoint.__qualname__}",
            "include_in_schema": route.include_in_schema,
            "dependencies": [d.dependency.__name__ for d in route.dependencies],
        }
        for route in web_routes(application.routes)
    ]


def test_web_route_inventory(web_env):
    """Routes, methods, endpoint names, auth dependency and include_in_schema — in registration order.

    Order is behavior (first match wins, e.g. ``/clusters/new`` before ``/clusters/{cluster_id}``).
    """
    application, engine = _make_stubbed_app(bypass_auth=False)
    try:
        inventory = _route_inventory(application)
    finally:
        engine.dispose()
    assert len(inventory) == len(web_routes(web_router.routes)) == 85
    assert all(r["include_in_schema"] is False for r in inventory)
    assert_golden_json("web/routes.json", inventory)


# ── 2. GET sweep ──────────────────────────────────────────────────────────────────────────────

HX = {"HX-Request": "true"}


@dataclass(frozen=True)
class GetCase:
    """One GET request against a freshly seeded app."""

    name: str  # golden file stem; also the template-context key
    url: str
    headers: dict = field(default_factory=dict)


C = GetCase
GET_CASES: tuple[GetCase, ...] = (
    # activity
    C("activity_list", "/activity"),
    C("activity_list__filtered", "/activity?entity_type=cluster&source=scheduler&severity=warning"),
    C("activity_page", "/activity/page"),
    C("activity_page__before", f"/activity/page?before={T - 8 * H}&entity_type=&source=&severity="),
    # alerts
    C("alert_list", "/alerts"),
    C("alert_list__open", "/alerts?status=open"),
    C("alert_list__resolved_cluster", f"/alerts?status=resolved&cluster_id={OUTDOOR}"),
    C("alert_list__plant", f"/alerts?plant_id={ALOCASIA}"),
    C("alert_badge", "/alerts/badge"),
    # analytics
    C("cluster_history", f"/clusters/{INDOOR}/history"),
    C("cluster_history__outdoor_72h_limit3", f"/clusters/{OUTDOOR}/history?hours=72&limit=3"),
    C("cluster_history__empty", f"/clusters/{EMPTY}/history"),
    C("cluster_history__404", "/clusters/999/history"),
    C("cluster_history__422", f"/clusters/{INDOOR}/history?hours=0"),
    C("cluster_stats", f"/clusters/{INDOOR}/stats"),
    C("cluster_stats__30d", f"/clusters/{INDOOR}/stats?days=30"),
    C("cluster_stats__404", "/clusters/999/stats"),
    C("cluster_stats_export", f"/clusters/{INDOOR}/stats/export"),
    C("cluster_stats_export__no_irrigator", f"/clusters/{EMPTY}/stats/export"),
    C("cluster_learn", f"/clusters/{INDOOR}/learn"),
    C("cluster_learn__outdoor", f"/clusters/{OUTDOOR}/learn"),
    C("cluster_learn__empty", f"/clusters/{EMPTY}/learn"),
    C("scheduler_page", "/scheduler"),
    # auth
    C("login_form", "/login"),
    C("login_form__next", "/login?next=/alerts%3Fstatus%3Dopen"),
    C("login_form__offsite_next", "/login?next=https://evil.example/steal"),
    # clusters
    C("list_clusters", "/clusters"),
    C("new_cluster_form", "/clusters/new"),
    C("edit_cluster_form", f"/clusters/{INDOOR}/edit"),
    C("edit_cluster_form__404", "/clusters/999/edit"),
    C("cluster_detail", f"/clusters/{INDOOR}"),
    C("cluster_detail__72h", f"/clusters/{INDOOR}?hours=72"),
    C("cluster_detail__outdoor", f"/clusters/{OUTDOOR}"),
    C("cluster_detail__empty", f"/clusters/{EMPTY}"),
    C("cluster_detail__404", "/clusters/999"),
    C("cluster_status_fragment", f"/clusters/{INDOOR}/status-fragment"),
    C("cluster_status_fragment__outdoor", f"/clusters/{OUTDOOR}/status-fragment"),
    C("cluster_status_fragment__404", "/clusters/999/status-fragment"),
    C("cluster_card_fragment", f"/clusters/{INDOOR}/card-fragment"),
    C("cluster_card_fragment__outdoor", f"/clusters/{OUTDOOR}/card-fragment"),
    C("cluster_card_fragment__empty", f"/clusters/{EMPTY}/card-fragment"),
    C("cluster_chart_fragment", f"/clusters/{INDOOR}/chart-fragment"),
    C("cluster_chart_fragment__temperature_48h", f"/clusters/{INDOOR}/chart-fragment?metric=temperature&hours=48"),
    C("cluster_chart_fragment__light_outdoor", f"/clusters/{OUTDOOR}/chart-fragment?metric=light"),
    C("cluster_chart_fragment__bad_metric", f"/clusters/{INDOOR}/chart-fragment?metric=bogus"),
    C("cluster_chart_fragment__404", "/clusters/999/chart-fragment"),
    C("cluster_overlay_fragment", f"/clusters/{INDOOR}/overlay-fragment"),
    C("cluster_overlay_fragment__24h", f"/clusters/{OUTDOOR}/overlay-fragment?hours=24"),
    C("cluster_overlay_fragment__404", "/clusters/999/overlay-fragment"),
    C("cluster_heatmap_fragment", f"/clusters/{INDOOR}/heatmap-fragment"),
    C("cluster_heatmap_fragment__7d", f"/clusters/{OUTDOOR}/heatmap-fragment?days=7"),
    C("cluster_heatmap_fragment__404", "/clusters/999/heatmap-fragment"),
    # configs
    C("config_form", f"/clusters/{INDOOR}/config"),
    C("config_form__404", "/clusters/999/config"),
    # decisions / efficacy
    C("cluster_decisions", f"/clusters/{INDOOR}/decisions"),
    C("cluster_decisions__limit1", f"/clusters/{INDOOR}/decisions?limit=1"),
    C("cluster_decisions__404", "/clusters/999/decisions"),
    C("cluster_efficacy_page", f"/clusters/{INDOOR}/efficacy"),
    C("cluster_efficacy_page__outdoor_30d", f"/clusters/{OUTDOOR}/efficacy?days=30"),
    # fragments / health
    C("health_badge", "/health/badge"),
    C("dashboard_hero", "/dashboard/hero"),
    C("health_page", "/health"),
    # irrigators
    C("list_irrigators", f"/clusters/{INDOOR}/irrigators"),
    C("new_irrigator_form__has_irrigator", f"/clusters/{INDOOR}/irrigators/new"),
    C("new_irrigator_form", f"/clusters/{EMPTY}/irrigators/new"),
    C("edit_irrigator_form", f"/clusters/{INDOOR}/irrigators/edit"),
    C("edit_irrigator_form__outdoor", f"/clusters/{OUTDOOR}/irrigators/edit"),
    C("edit_irrigator_form__404", f"/clusters/{EMPTY}/irrigators/edit"),
    C("log_manual_form", f"/irrigators/{IRR_INDOOR}/log-manual"),
    C("log_manual_form__404", "/irrigators/999/log-manual"),
    # operations (GET)
    C("monitor", f"/clusters/{INDOOR}/monitor"),
    C("monitor__outdoor", f"/clusters/{OUTDOOR}/monitor"),
    C("monitor__empty", f"/clusters/{EMPTY}/monitor"),
    C("monitor__404", "/clusters/999/monitor"),
    # pages
    C("dashboard", "/"),
    # plant dashboard
    C("plant_dashboard", f"/clusters/{INDOOR}/plants/{MONSTERA}"),
    C("plant_dashboard__critically_dry", f"/clusters/{INDOOR}/plants/{ALOCASIA}"),
    C("plant_dashboard__outdoor_72h", f"/clusters/{OUTDOOR}/plants/{LOQUAT}?hours=72"),
    C("plant_dashboard__wrong_cluster", f"/clusters/{OUTDOOR}/plants/{MONSTERA}"),
    C("plant_chart_fragment", f"/clusters/{INDOOR}/plants/{MONSTERA}/chart-fragment"),
    C(
        "plant_chart_fragment__humidity_48h",
        f"/clusters/{INDOOR}/plants/{ALOCASIA}/chart-fragment?metric=env_humidity&hours=48",
    ),
    C("plant_chart_fragment__bad_metric", f"/clusters/{INDOOR}/plants/{MONSTERA}/chart-fragment?metric=bogus"),
    C("plant_chart_fragment__404", f"/clusters/{INDOOR}/plants/{LOQUAT}/chart-fragment"),
    C("plant_health_fragment", f"/clusters/{INDOOR}/plants/{MONSTERA}/health-fragment"),
    C("plant_health_fragment__no_history", f"/clusters/{OUTDOOR}/plants/{LOQUAT}/health-fragment"),
    C("plant_health_fragment__404", f"/clusters/{INDOOR}/plants/{LOQUAT}/health-fragment"),
    # plants
    C("list_plants", f"/clusters/{INDOOR}/plants"),
    C("list_plants__404", "/clusters/999/plants"),
    C("new_plant_form", f"/clusters/{INDOOR}/plants/new"),
    C("edit_plant_form", f"/clusters/{INDOOR}/plants/{MONSTERA}/edit"),
    C("edit_plant_form__404", f"/clusters/{OUTDOOR}/plants/{MONSTERA}/edit"),
    # preferences / quality
    C("preferences_page", "/preferences"),
    C("preferences_page__saved", "/preferences?saved=1"),
    C("preferences_page__saved_global", "/preferences?saved=global"),
    C("quality_page", "/quality"),
    # sensors
    C("list_sensors", f"/clusters/{INDOOR}/sensors"),
    C("new_sensor_form", f"/clusters/{INDOOR}/sensors/new"),
    C("new_sensor_form__empty", f"/clusters/{EMPTY}/sensors/new"),
    C("edit_sensor_form", f"/clusters/{INDOOR}/sensors/{S_MONSTERA}/edit"),
    C("edit_sensor_form__unassigned", f"/clusters/{INDOOR}/sensors/{S_AMBIENT}/edit"),
    C("edit_sensor_form__404", f"/clusters/{OUTDOOR}/sensors/{S_MONSTERA}/edit"),
    # vacation
    C("vacation_list", "/vacation"),
    C("edit_vacation_form", f"/vacation/{VACATION_UPCOMING}/edit"),
    C("edit_vacation_form__404", "/vacation/999/edit"),
    # windows
    C("edit_window_form", f"/clusters/{OUTDOOR}/windows/{WINDOW}/edit"),
    C("edit_window_form__404", f"/clusters/{INDOOR}/windows/{WINDOW}/edit"),
    # error-template branch on HX-Request (the only route-level is_hx switch)
    C("cluster_detail__404__hx", "/clusters/999", HX),
    C("cluster_detail__422", "/clusters/not-a-number"),
    C("cluster_detail__422__hx", "/clusters/not-a-number", HX),
    C("dashboard__hx", "/", HX),
)


def _run_get(client: TestClient, case: GetCase):
    return client.get(case.url, headers=case.headers, follow_redirects=False)


def test_get_cases_are_unique():
    names = [c.name for c in GET_CASES]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("case", GET_CASES, ids=lambda c: c.name)
def test_get_route_html_golden(case, seeded_get_client):
    """Full response (status, pinned headers, body) of one GET against the seeded greenhouse."""
    resp = _run_get(seeded_get_client, case)
    assert_golden(f"web/get/{case.name}.html", render_response("GET", case.url, case.headers, resp))


def test_get_sweep_covers_every_web_get_route():
    """Every GET route of the web router is hit by at least one case (path template match)."""
    import re

    get_paths = [r.path for r in web_routes(web_router.routes) if "GET" in r.methods]
    case_paths = [c.url.split("?")[0] for c in GET_CASES]
    for path in get_paths:
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"
        assert any(re.match(pattern, p) for p in case_paths), path


# ── State variants: branches the base seed does not reach ─────────────────────────────────────


def _chrome_flags(repo: IrrigationRepository) -> None:
    """Every base-layout banner on: global dry-run, scheduler paused, an active vacation, light theme."""
    repo.update_preferences(dry_run_global=True, scheduler_paused=True, theme="light")
    repo.add_vacation_window(T - 24 * H, T + 48 * H, contact_email="sitter@example.com", notes="Weekend away")


def _quiet_hours_now(repo: IrrigationRepository) -> None:
    repo.set_irrigation_config(cluster_id=INDOOR, quiet_start_hour=9, quiet_end_hour=12)


def _no_open_alerts(repo: IrrigationRepository) -> None:
    repo.resolve_alert(ALERT_OPEN)


def _sixty_activity_events(repo: IrrigationRepository) -> None:
    for i in range(60):
        repo.add_activity_event("web", "cluster", "note", f"Event {i}", entity_id=INDOOR, timestamp=T - 3 * H - i * 60)


VARIANTS = (
    ("dashboard__chrome_flags", "/", _chrome_flags, False),
    ("vacation_list__active", "/vacation", _chrome_flags, False),
    ("cluster_detail__chrome_flags", f"/clusters/{INDOOR}", _chrome_flags, False),
    ("cluster_detail__quiet_hours_now", f"/clusters/{INDOOR}", _quiet_hours_now, False),
    ("alert_badge__none_open", "/alerts/badge", _no_open_alerts, False),
    ("activity_list__paginated", "/activity", _sixty_activity_events, False),
    ("activity_page__paginated", "/activity/page", _sixty_activity_events, False),
    ("scheduler_page__running", "/scheduler", None, True),
    ("health_badge__running", "/health/badge", None, True),
)


@pytest.mark.parametrize(("name", "url", "setup", "running"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_get_variant_html_golden(name, url, setup, running, seeded_get_client):
    """Same golden format as the sweep, after a state tweak (or with the scheduler running, paused)."""
    from greenhouse_server.scheduler import start_scheduler, stop_scheduler

    if setup is not None:
        session = seeded_get_client.app.state.session_factory()
        try:
            setup(IrrigationRepository(session))
            session.commit()
        finally:
            session.close()
    if running:
        start_scheduler(paused=True)
    try:
        resp = seeded_get_client.get(url, follow_redirects=False)
    finally:
        if running:
            stop_scheduler()
    assert_golden(f"web/get/{name}.html", render_response("GET", url, {}, resp))


# ── template name + context keys per GET ─────────────────────────────────────────────────────


def test_template_context_golden(web_env, monkeypatch):
    """``{case: [[template, sorted context keys], …]}`` for the whole GET sweep (reset app per case)."""
    spy = TemplateSpy()
    spy.install(monkeypatch)
    observed: dict[str, list] = {}
    for case in GET_CASES:
        client = TestClient(reset_shared_app(), raise_server_exceptions=False)
        spy.calls.clear()
        _run_get(client, case)
        observed[case.name] = list(spy.calls)
    assert_golden_json("web/template_context.json", observed)
