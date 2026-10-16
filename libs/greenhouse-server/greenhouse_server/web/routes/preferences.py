"""User preferences web routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from greenhouse_core.repository import GLOBAL_CONFIG_DEFAULTS
from greenhouse_server import state
from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.scheduler import apply_timezone_preference
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import parsed_or_400
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)

_ALLOWED_THEMES = {"auto", "light", "dark"}


@dataclass(frozen=True, slots=True)
class _PreferencesForm:
    """``POST /preferences``: the form fields; a checkbox posts a value only when ticked."""

    units: Annotated[str, Form()]
    timezone: Annotated[str, Form()]
    theme: Annotated[str, Form()]
    refresh_interval_seconds: Annotated[int, Form()]
    default_cluster_id: Annotated[str, Form()] = ""
    dry_run_global: Annotated[str, Form()] = ""
    notify_manual: Annotated[str, Form()] = ""
    notify_emergency: Annotated[str, Form()] = ""
    notify_alerts: Annotated[str, Form()] = ""
    notify_auto: Annotated[str, Form()] = ""


@router.get("/preferences")
def preferences_page(request: Request, repo: RepoDep) -> Response:
    """Render the preferences page with the global irrigation defaults."""
    prefs = repo.get_preferences()
    global_config = repo.get_global_irrigation_config()
    repo.commit()
    clusters = repo.list_clusters()
    settings = state.settings(request.app)
    ntfy_configured = bool(settings.ntfy_server_url and settings.ntfy_topic)
    return templates.TemplateResponse(
        request,
        "preferences.html",
        base_context(
            request,
            prefs=prefs,
            clusters=clusters,
            global_config=global_config,
            builtin_config=GLOBAL_CONFIG_DEFAULTS,
            ntfy_configured=ntfy_configured,
            saved=request.query_params.get("saved"),
        ),
    )


@router.post("/preferences")
def update_preferences(request: Request, repo: RepoDep, form: Annotated[_PreferencesForm, Depends()]) -> Response:
    """Save the preferences form, re-sync the clocks to its timezone and reload the page."""
    # Blank clears the default; a non-integer is a 400 page (it used to clear silently).
    default_cluster = parsed_or_400(form.default_cluster_id, int, error="Invalid default_cluster_id")
    if default_cluster is not None:
        require_cluster(repo, default_cluster)  # 404 page, as the API, before anything is written
    prefs = repo.update_preferences(
        units=form.units,
        timezone=form.timezone,
        theme=form.theme,
        refresh_interval_seconds=form.refresh_interval_seconds,
        default_cluster_id=default_cluster,
        dry_run_global=bool(form.dry_run_global),
        notify_manual=bool(form.notify_manual),
        notify_emergency=bool(form.notify_emergency),
        notify_alerts=bool(form.notify_alerts),
        notify_auto=bool(form.notify_auto),
    )
    repo.commit()
    apply_timezone_preference(request, prefs.timezone)
    return RedirectResponse(url="/preferences?saved=1", status_code=303)


@router.post("/preferences/theme")
def update_theme(repo: RepoDep, theme: Annotated[str, Form()]) -> Response:
    """Persist just the theme preference for the header light/dark toggle.

    The header toggle paints instantly from ``localStorage``; this fire-and-forget
    endpoint records the same choice server-side so a reload renders the matching
    theme instead of falling back to the stale stored preference.

    Args:
        repo: The request's repository (the preferences row is written through it).
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
    repo.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
