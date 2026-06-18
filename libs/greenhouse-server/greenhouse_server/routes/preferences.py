"""User preferences routes."""

from fastapi import APIRouter, Request

from greenhouse_core.schemas import PreferencesResponse, PreferencesUpdateRequest
from greenhouse_server.deps import RepoDep
from greenhouse_server.scheduler import apply_timezone_preference

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("", response_model=PreferencesResponse, summary="Get user preferences")
def get_preferences(repo: RepoDep):
    """Return the current user preferences, creating defaults on first access.

    Returns:
        The singleton preferences row with units, timezone, theme, default cluster,
        refresh interval, and dry-run flag.
    """
    prefs = repo.get_preferences()
    repo.session.commit()
    return prefs


@router.put("", response_model=PreferencesResponse, summary="Update user preferences")
def update_preferences(request: PreferencesUpdateRequest, http_request: Request, repo: RepoDep):
    """Patch user preferences; omitted fields are left unchanged.

    Changing the timezone re-syncs the scheduler, weather, and display clocks to
    the new zone so they keep agreeing with the decision engine.

    Args:
        request: Partial update — any combination of units, timezone, theme,
            default_cluster_id, refresh_interval_seconds, or dry_run_global.
            Fields set to null are ignored.

    Returns:
        The updated preferences row.
    """
    prefs = repo.update_preferences(**request.model_dump(exclude_none=True))
    repo.session.commit()
    apply_timezone_preference(http_request, prefs.timezone)
    return prefs
