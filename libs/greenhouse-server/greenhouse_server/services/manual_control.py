"""Manual irrigator start/stop/log: the one code path behind the API and the web UI.

Both front doors (``POST /api/v1/irrigators/{id}/start|stop|log-manual`` and
the web ``/irrigators/{id}/start|stop|log-manual`` actions) call these
functions, so a manual action always gets the same rails no matter where it
came from: per-day caps, the global dry-run refusal, the post-watering leak
check, the dry-run pump watcher, the event row, and the notification. Stops are never refused. A manual log
(no hardware) is recorded as a ``start`` row so cooldown, caps and learning
count it like any other run.

Errors are raised as :class:`ManualActionError` carrying the HTTP status the
JSON API returns; each route maps it to its own response shape.
"""

import time
from typing import TYPE_CHECKING

from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.models import (
    ENTITY_IRRIGATOR,
    EVENT_ACTION_START,
    EVENT_ACTION_STOP,
    SOURCE_IRRIGATION,
    TRIGGERED_BY_MANUAL,
    Irrigator,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.caps import daily_cap_violation
from greenhouse_server.services.irrigation import schedule_pump_watcher, stop_unrecorded_start
from greenhouse_server.services.irrigation_jobs import (
    _schedule_leak_check,
    leak_check_after_seconds,
    record_leak_check_armed,
    record_leak_check_skipped,
)
from greenhouse_server.services.notify import NtfyClient, maybe_notify

if TYPE_CHECKING:
    from greenhouse_core.devices import AbstractIrrigatorAdapter


DRY_RUN_REFUSED_DETAIL = "global dry run is on: irrigator not started"
MINUTES_TOO_SMALL_DETAIL = "minutes must be at least 1"
START_NOT_RECORDED_DETAIL = "the start could not be recorded, so the irrigator was stopped again"


class ManualActionError(Exception):
    """A manual start/stop was refused or failed.

    Attributes:
        status_code: HTTP status the JSON API responds with (422 bad
            minutes, 409 caps or global dry run, 503 no registry / unknown
            model, 502 device failure, 500 start not recorded).
        detail: Human-readable reason.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_rate_limits(repo: IrrigationRepository, cluster_id: int, irrigator_id: int, minutes: int | None) -> None:
    """Refuse a start that would exceed the cluster's effective per-day caps.

    The caps resolve cluster row over global row (B-4: the global caps used to be ignored); the
    automatic pipeline applies the same rule (:func:`.caps.daily_cap_violation`) as a skip.

    Args:
        repo: Active repository session.
        cluster_id: Cluster whose effective config holds the caps.
        irrigator_id: Irrigator being started (duration cap is per-irrigator).
        minutes: Requested duration; ``None`` counts as 0 for the duration cap.

    Raises:
        ManualActionError: 409 if ``max_events_per_day`` or
            ``daily_cap_minutes`` would be exceeded.
    """
    violation = daily_cap_violation(repo, cluster_id, irrigator_id, minutes)
    if violation is not None:
        raise ManualActionError(409, violation)


def _adapter(registry: DeviceRegistry | None, irrigator: Irrigator) -> "AbstractIrrigatorAdapter":
    if registry is None:
        raise ManualActionError(503, "No device registry (missing Tuya credentials)")
    try:
        return registry.get_irrigator(irrigator)
    except UnknownDeviceModel as exc:
        raise ManualActionError(503, str(exc)) from exc


DRY_RUN_REFUSED_ACTIVITY_CODE = "dry_run_global"


def _refuse_if_dry_run(repo: IrrigationRepository, irrigator: Irrigator, minutes: int | None, *, via: str) -> None:
    """Refuse the start while the global dry run is on, leaving a durable activity row (S1).

    The preference promises that no pump runs, so a manual start is evaluated through every
    rail and then refused instead of actuating. The activity row is committed before raising
    because the route turns the refusal into an HTTP error and keeps nothing else.

    Raises:
        ManualActionError: 409 when ``dry_run_global`` is on.
    """
    if not repo.get_preferences().dry_run_global:
        return
    duration = f" for {minutes} min" if minutes else ""
    repo.add_activity_event(
        source=SOURCE_IRRIGATION,
        entity_type=ENTITY_IRRIGATOR,
        entity_id=irrigator.id,
        code=DRY_RUN_REFUSED_ACTIVITY_CODE,
        message=f"global dry run: manual start{duration} on '{irrigator.name}' via {via} refused (pump not started)",
        severity="warning",
        payload={"irrigator_id": irrigator.id, "duration_minutes": minutes, "via": via},
    )
    repo.commit()
    raise ManualActionError(409, DRY_RUN_REFUSED_DETAIL)


def _record_start(
    repo: IrrigationRepository, irrigator: Irrigator, started_at: int, minutes: int | None, *, via: str
) -> int | None:
    """Write and commit the ``start`` event and the leak-check plan; returns the check's delay (or None)."""
    repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action=EVENT_ACTION_START,
        duration_minutes=minutes,
        triggered_by=TRIGGERED_BY_MANUAL,
        notes=f"Manual start via {via} ({minutes} min)" if minutes else f"Manual start via {via}",
        timestamp=started_at,
    )
    leak_check_after = _record_leak_check_plan(repo, irrigator, started_at, minutes)
    repo.commit()
    return leak_check_after


def _record_leak_check_plan(
    repo: IrrigationRepository, irrigator: Irrigator, started_at: int, minutes: int | None
) -> int | None:
    """Arm (or say why not) the post-watering leak check of a manual start; the caller commits.

    A manual start is a real watering, so it gets the check like any start — due when the run has
    ended plus the settle time (:func:`leak_check_after_seconds`). The ``leak_check_armed`` marker,
    committed with the event, is what the restart re-arm finds (a hand log has none). Without a
    duration the device timer decides how long the pump runs, and no device exposes its maximum
    run bound, so there is no safe time to judge the soil: the check is skipped and the activity
    log says why.

    Returns:
        Seconds after the start when the check runs, or None when it is skipped.
    """
    if not minutes:
        record_leak_check_skipped(
            repo,
            irrigator.cluster_id,
            started_at,
            reason="manual start without a duration (the device timer decides how long it runs)",
        )
        return None
    after_seconds = leak_check_after_seconds(minutes)
    record_leak_check_armed(repo, irrigator, started_at, triggered_by=TRIGGERED_BY_MANUAL, after_seconds=after_seconds)
    return after_seconds


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

    Order: registry → caps check → adapter → global dry-run refusal → device start → ``start`` event
    and the leak-check marker (committed) → leak check (due after the run plus settle time; skipped
    without a duration) → dry-run watcher (only with a duration — it needs a deadline) →
    notification. A
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
        ManualActionError: 422 ``minutes`` below 1, 409 caps or global dry run, 503 no registry /
            unknown model, 502 device failure, 500 the start could not be recorded (the pump was
            stopped again).
    """
    if minutes is not None and minutes < 1:  # the API schema refuses these first; the web form lands here
        raise ManualActionError(422, MINUTES_TOO_SMALL_DETAIL)
    if registry is None:  # checked before the caps, as the API always has
        raise ManualActionError(503, "No device registry (missing Tuya credentials)")
    check_rate_limits(repo, irrigator.cluster_id, irrigator.id, minutes)
    adapter = _adapter(registry, irrigator)
    _refuse_if_dry_run(repo, irrigator, minutes, via=via)

    irrigator_id = irrigator.id  # read before any write can fail
    success, output = adapter.start(irrigator, minutes)
    if not success:
        raise ManualActionError(502, output)

    started_at = int(time.time())
    try:
        leak_check_after = _record_start(repo, irrigator, started_at, minutes, via=via)
    except Exception as exc:
        # The pump runs but nothing records it: stop it rather than leave an unrecorded run.
        stop_unrecorded_start(repo, adapter, irrigator, irrigator_id)
        raise ManualActionError(500, START_NOT_RECORDED_DETAIL) from exc
    if leak_check_after is not None:
        _schedule_leak_check(irrigator.cluster_id, started_at, after_seconds=leak_check_after)
    if minutes:
        schedule_pump_watcher(irrigator.id, minutes, started_at, triggered_by=TRIGGERED_BY_MANUAL)
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "manual",
        lambda n: n.notify_irrigation(
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
        lambda n: n.notify_irrigation(
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
        lambda n: n.notify_irrigation(
            triggered_by=TRIGGERED_BY_MANUAL,
            irrigator_name=irrigator.name,
            duration_minutes=minutes,
            detail="logged (watered by hand)",
        ),
    )
    return event_id
