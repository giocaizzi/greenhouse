"""Manual irrigator start/stop/log: the one code path behind the API and the web UI.

Both front doors (``POST /api/v1/irrigators/{id}/start|stop|log-manual`` and
the web ``/irrigators/{id}/start|stop|log-manual`` actions) call these
functions, so a manual action always gets the same rails no matter where it
came from: per-day caps, the dry-run pump watcher, the event row, and the
notification. (The web routes used to drive the adapter directly — or, for
log-manual, record an ``action="manual"`` row that cooldown, caps and
learning never counted — and skipped all of them.)

Errors are raised as :class:`ManualActionError` carrying the HTTP status the
JSON API returns; each route maps it to its own response shape.
"""

import time
from typing import TYPE_CHECKING

from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.models import EVENT_ACTION_START, EVENT_ACTION_STOP, TRIGGERED_BY_MANUAL, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.irrigation import schedule_pump_watcher
from greenhouse_server.services.notify import NtfyClient, maybe_notify

if TYPE_CHECKING:
    from greenhouse_core.devices import AbstractIrrigatorAdapter


class ManualActionError(Exception):
    """A manual start/stop was refused or failed.

    Attributes:
        status_code: HTTP status the JSON API responds with (409 caps,
            503 no registry / unknown model, 502 device failure).
        detail: Human-readable reason.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_rate_limits(repo: IrrigationRepository, cluster_id: int, irrigator_id: int, minutes: int | None) -> None:
    """Refuse a start that would exceed the cluster's per-day caps.

    Args:
        repo: Active repository session.
        cluster_id: Cluster whose config holds the caps.
        irrigator_id: Irrigator being started (duration cap is per-irrigator).
        minutes: Requested duration; ``None`` counts as 0 for the duration cap.

    Raises:
        ManualActionError: 409 if ``max_events_per_day`` or
            ``daily_cap_minutes`` would be exceeded.
    """
    config = repo.get_irrigation_config(cluster_id)
    if not config:
        return

    requested = minutes or 0

    if config.max_events_per_day is not None:
        # Count "start" events in the last 24 h for the cluster's single irrigator.
        irrigator = repo.get_irrigator_for_cluster(cluster_id)
        total_starts = (
            sum(1 for e in repo.get_recent_events(irrigator.id, hours=24) if e.action == EVENT_ACTION_START)
            if irrigator is not None
            else 0
        )
        if total_starts >= config.max_events_per_day:
            raise ManualActionError(409, "cluster max_events_per_day reached")

    if config.daily_cap_minutes is not None:
        recent = repo.get_recent_events(irrigator_id, hours=24)
        minutes_used = sum(e.duration_minutes or 0 for e in recent if e.action == EVENT_ACTION_START)
        if minutes_used + requested > config.daily_cap_minutes:
            raise ManualActionError(409, "irrigator daily cap reached")


def _adapter(registry: DeviceRegistry | None, irrigator: Irrigator) -> "AbstractIrrigatorAdapter":
    if registry is None:
        raise ManualActionError(503, "No device registry (missing Tuya credentials)")
    try:
        return registry.get_irrigator(irrigator)
    except UnknownDeviceModel as exc:
        raise ManualActionError(503, str(exc)) from exc


def manual_start(
    repo: IrrigationRepository,
    registry: DeviceRegistry | None,
    notifier: NtfyClient | None,
    irrigator: Irrigator,
    minutes: int | None,
    *,
    via: str,
) -> str:
    """Start ``irrigator`` by hand, with every manual-start safety rail.

    Order: registry → caps check → adapter → device start → ``start`` event (committed) → dry-run
    watcher (only with a duration — it needs a deadline) → notification. A
    failed device start records nothing, matching the JSON API. Commits because
    the ``start`` event must be durable before the side effects that follow it
    (the watcher job, which runs in its own session, and the notification).

    Args:
        repo: Active repository session (committed here on success).
        registry: Device registry, or None when Tuya credentials are missing.
        notifier: ntfy client, or None when notifications are unconfigured.
        irrigator: Irrigator to start.
        minutes: Optional run duration.
        via: Front door for the event note (``"API"`` or ``"web UI"``).

    Returns:
        The adapter's success message.

    Raises:
        ManualActionError: 409 caps, 503 no registry / unknown model, 502 device failure.
    """
    if registry is None:  # checked before the caps, as the API always has
        raise ManualActionError(503, "No device registry (missing Tuya credentials)")
    check_rate_limits(repo, irrigator.cluster_id, irrigator.id, minutes)
    adapter = _adapter(registry, irrigator)

    success, output = adapter.start(irrigator, minutes)
    if not success:
        raise ManualActionError(502, output)

    started_at = int(time.time())
    repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action=EVENT_ACTION_START,
        duration_minutes=minutes,
        triggered_by=TRIGGERED_BY_MANUAL,
        notes=f"Manual start via {via} ({minutes} min)" if minutes else f"Manual start via {via}",
        timestamp=started_at,
    )
    repo.commit()
    if minutes:
        schedule_pump_watcher(irrigator.id, minutes, started_at, triggered_by=TRIGGERED_BY_MANUAL)
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "manual",
        lambda: notifier.notify_irrigation(  # type: ignore[union-attr]  # maybe_notify returns first when notifier is None
            triggered_by=TRIGGERED_BY_MANUAL,
            irrigator_name=irrigator.name,
            duration_minutes=minutes,
            detail="started",
        ),
    )
    return output


def manual_stop(
    repo: IrrigationRepository,
    registry: DeviceRegistry | None,
    notifier: NtfyClient | None,
    irrigator: Irrigator,
    *,
    via: str,
) -> str:
    """Stop ``irrigator`` by hand: device stop → ``stop`` event (committed) → notification.

    Commits because the device has already stopped: the event records a hardware
    side effect and must be durable before the notification goes out.

    Args:
        repo: Active repository session (committed here on success).
        registry: Device registry, or None when Tuya credentials are missing.
        notifier: ntfy client, or None when notifications are unconfigured.
        irrigator: Irrigator to stop.
        via: Front door for the event note (``"API"`` or ``"web UI"``).

    Returns:
        The adapter's success message.

    Raises:
        ManualActionError: 503 no registry / unknown model, 502 device failure.
    """
    adapter = _adapter(registry, irrigator)
    success, output = adapter.stop(irrigator)
    if not success:
        raise ManualActionError(502, output)

    repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action=EVENT_ACTION_STOP,
        triggered_by=TRIGGERED_BY_MANUAL,
        notes=f"Manual stop via {via}",
    )
    repo.commit()
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "manual",
        lambda: notifier.notify_irrigation(  # type: ignore[union-attr]  # maybe_notify returns first when notifier is None
            triggered_by=TRIGGERED_BY_MANUAL,
            irrigator_name=irrigator.name,
            detail="stopped",
        ),
    )
    return output


def manual_log(
    repo: IrrigationRepository,
    notifier: NtfyClient | None,
    irrigator: Irrigator,
    minutes: int,
    notes: str | None = None,
) -> int:
    """Record a watering done by hand — no hardware is touched.

    Recorded as a ``start`` event (``triggered_by="manual"``) so the cooldown,
    per-day caps, learning and efficacy all see it, exactly like a manual
    start; the per-day caps apply. Commits before notifying, like
    :func:`manual_start`, so the push never announces an unsaved event.

    Args:
        repo: Active repository session (committed here on success).
        notifier: ntfy client, or None when notifications are unconfigured.
        irrigator: Irrigator the hand-watering is attributed to.
        minutes: How long the user watered.
        notes: Optional free-text note.

    Returns:
        The new irrigation event id.

    Raises:
        ManualActionError: 409 if the cluster's per-day caps would be exceeded.
    """
    check_rate_limits(repo, irrigator.cluster_id, irrigator.id, minutes)
    event_id = repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action=EVENT_ACTION_START,
        duration_minutes=minutes,
        triggered_by=TRIGGERED_BY_MANUAL,
        notes=notes or f"Manual ({minutes} min)",
    )
    repo.commit()
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "manual",
        lambda: notifier.notify_irrigation(  # type: ignore[union-attr]  # maybe_notify returns first when notifier is None
            triggered_by=TRIGGERED_BY_MANUAL,
            irrigator_name=irrigator.name,
            duration_minutes=minutes,
            detail="logged (watered by hand)",
        ),
    )
    return event_id
