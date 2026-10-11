"""Per-day irrigation caps: the one rule behind the manual start refusal and the automatic skip.

``max_events_per_day`` bounds how many ``start`` events the cluster's irrigator may log in the
last ``DAILY_CAP_WINDOW_HOURS``; ``daily_cap_minutes`` bounds the minutes it may run in that
window, the requested run included. Both resolve through the hierarchical config (cluster row
over the global row), so a cap set only globally binds every cluster that does not set its own.
The manual start (:mod:`.manual_control`) refuses with 409 and the automatic pipeline
(:mod:`.irrigation`) skips with ``TriggerCode.DAILY_CAP_HIT`` on the same verdict.
"""

from greenhouse_core.constants import DAILY_CAP_WINDOW_HOURS
from greenhouse_core.models import EVENT_ACTION_START
from greenhouse_core.repository import IrrigationRepository

MAX_EVENTS_REACHED = "cluster max_events_per_day reached"
DAILY_MINUTES_REACHED = "irrigator daily cap reached"


def _effective_cap(repo: IrrigationRepository, cluster_id: int, field: str) -> int | None:
    """A cap's effective value (cluster → global → none); a non-integer value means no cap."""
    value = repo.get_effective_config(cluster_id)[field]["value"]
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _recent_starts(repo: IrrigationRepository, irrigator_id: int) -> list[int]:
    """Durations (0 when unset) of the irrigator's ``start`` events inside the cap window."""
    return [
        e.duration_minutes or 0
        for e in repo.get_recent_events(irrigator_id, hours=DAILY_CAP_WINDOW_HOURS)
        if e.action == EVENT_ACTION_START
    ]


def daily_cap_violation(
    repo: IrrigationRepository, cluster_id: int, irrigator_id: int, minutes: int | None
) -> str | None:
    """Which per-day cap a start of ``minutes`` would break, or None when it fits.

    Args:
        repo: Active repository session.
        cluster_id: Cluster whose effective config holds the caps.
        irrigator_id: Irrigator being started (the duration cap is per irrigator).
        minutes: Requested duration; ``None`` counts as 0 for the duration cap.

    Returns:
        The refusal wording (shared by the API's 409 and the automatic skip reason), or None.
    """
    max_events = _effective_cap(repo, cluster_id, "max_events_per_day")
    if max_events is not None:
        # Counted on the cluster's single irrigator, as the manual path always has.
        irrigator = repo.get_irrigator_for_cluster(cluster_id)
        starts = len(_recent_starts(repo, irrigator.id)) if irrigator is not None else 0
        if starts >= max_events:
            return MAX_EVENTS_REACHED

    cap_minutes = _effective_cap(repo, cluster_id, "daily_cap_minutes")
    if cap_minutes is not None and sum(_recent_starts(repo, irrigator_id)) + (minutes or 0) > cap_minutes:
        return DAILY_MINUTES_REACHED
    return None
