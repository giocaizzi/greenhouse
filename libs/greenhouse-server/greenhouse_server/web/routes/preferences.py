"""User preferences web routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from greenhouse_server.deps import RepoDep
from greenhouse_server.scheduler import apply_timezone_preference
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)

_ALLOWED_THEMES = {"auto", "light", "dark"}


@router.get("/preferences")
def preferences_page(request: Request, repo: RepoDep):
    prefs = repo.get_preferences()
    global_config = repo.get_global_irrigation_config()
    repo.session.commit()
    clusters = repo.list_clusters()
    settings = request.app.state.settings
    ntfy_configured = bool(settings.ntfy_server_url and settings.ntfy_topic)
    return templates.TemplateResponse(
        request,
        "preferences.html",
        base_context(
            request,
            prefs=prefs,
            clusters=clusters,
            global_config=global_config,
            ntfy_configured=ntfy_configured,
            saved=request.query_params.get("saved"),
        ),
    )


@router.post("/preferences")
def update_preferences(
    request: Request,
    repo: RepoDep,
    units: str = Form(...),
    timezone: str = Form(...),
    theme: str = Form(...),
    refresh_interval_seconds: int = Form(...),
    default_cluster_id: str = Form(""),
    dry_run_global: str = Form(""),
    notify_manual: str = Form(""),
    notify_emergency: str = Form(""),
    notify_alerts: str = Form(""),
    notify_auto: str = Form(""),
):
    default_cluster: int | None = None
    if default_cluster_id.strip():
        try:
            default_cluster = int(default_cluster_id)
        except ValueError:
            default_cluster = None
    prefs = repo.update_preferences(
        units=units,
        timezone=timezone,
        theme=theme,
        refresh_interval_seconds=refresh_interval_seconds,
        default_cluster_id=default_cluster,
        dry_run_global=bool(dry_run_global),
        notify_manual=bool(notify_manual),
        notify_emergency=bool(notify_emergency),
        notify_alerts=bool(notify_alerts),
        notify_auto=bool(notify_auto),
    )
    repo.session.commit()
    apply_timezone_preference(request, prefs.timezone)
    return RedirectResponse(url="/preferences?saved=1", status_code=303)


@router.post("/preferences/theme")
def update_theme(repo: RepoDep, theme: str = Form(...)) -> Response:
    """Persist just the theme preference for the header light/dark toggle.

    The header toggle paints instantly from ``localStorage``; this fire-and-forget
    endpoint records the same choice server-side so a reload renders the matching
    theme instead of falling back to the stale stored preference.

    Args:
        theme: One of ``auto``, ``light``, or ``dark`` (the Preferences select's values).

    Returns:
        An empty ``204 No Content`` response on success.

    Raises:
        HTTPException: ``400`` if ``theme`` is not an allowed value.
    """
    if theme not in _ALLOWED_THEMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid theme: {theme!r}",
        )
    repo.update_preferences(theme=theme)
    repo.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
