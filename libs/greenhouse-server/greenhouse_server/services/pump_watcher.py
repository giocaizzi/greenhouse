"""Pump dry-run watcher.

Runs for the duration of an active irrigation. Polls DP 105 (the IK10PW's
water-shortage alarm) via the adapter's ``read_health`` surface; on the
first ``NO_WATER`` reading it immediately stops the pump, raises a typed
health alert through :class:`DeviceHealthMonitor`, and records an
``aborted`` irrigation event.

Why polling and not a persistent socket. The local protocol can push DP
changes asynchronously, but Tuya devices only accept one local TCP
connection at a time and the existing actuation path already opens one
transiently; juggling a persistent socket alongside cloud-API commands adds
reconnection logic without a meaningful latency win — the firmware itself
debounces dry-run detection over several seconds, so a 2 s poll is well
inside its own resolution.

Why we record through the monitor. The slow ambient observer and the
fast in-flight watchdog share a single dedup_key scheme
(``health:irrigator:{id}:no_water``). The watcher trips first (sub-2s
response is the safety story); the monitor's cache absorbs the
transition so the engine's actuation gate stays consistent with what the
watcher just observed.

False positives are safe (we stop before the pump is damaged); false
negatives are the danger. The signal is motor-current-based and has
documented quirks (a clogged filter mimics a dry pump), so for unattended
deployments a hardware float switch in the reservoir remains the
recommended belt-and-suspenders safeguard.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from greenhouse_core.constants import (
    PUMP_WATCHER_MAX_READ_FAILURES,
    PUMP_WATCHER_POLL_SECONDS,
    PUMP_WATCHER_WARMUP_SECONDS,
)
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import HealthAlarm
from greenhouse_core.devices.irrigators.ik10pw import IK10PW_ALARM_DP
from greenhouse_core.models import (
    ENTITY_IRRIGATOR,
    EVENT_ACTION_ABORTED,
    SOURCE_PUMP,
    TRIGGERED_BY_PUMP_WATCHER,
    Irrigator,
)
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import DeviceHealthMonitor

if TYPE_CHECKING:
    from greenhouse_core.devices.health import DeviceHealthState

logger = logging.getLogger(__name__)

ACTIVITY_CODE = "pump_dry_run"


class WatchOutcome(TypedDict):
    """Result of one watch; the key order is the order callers and logs see."""

    outcome: str
    polls: int
    read_failures: int
    alarm_raw: Any
    elapsed_seconds: float


def _outcome(kind: str, *, polls: int, failures: int, alarm_raw: object, elapsed: float) -> WatchOutcome:
    """Build a watch result; the caller reads the clock for elapsed at its own exit point."""
    return {
        "outcome": kind,
        "polls": polls,
        "read_failures": failures,
        "alarm_raw": alarm_raw,
        "elapsed_seconds": elapsed,
    }


@dataclass(frozen=True, slots=True)
class _Trip:
    """The tripped irrigator's identity, read once after the stop (survives a rollback's expiry)."""

    irrigator_id: int
    irrigator_name: str
    cluster_id: int


@dataclass(frozen=True, slots=True)
class _TripReading:
    """What the watcher saw when it tripped: the alarm DP value and how far into the cycle it was."""

    alarm_raw: object
    polls: int
    started_at: int
    duration_seconds: int
    elapsed_estimate: int


def _trip_payload(trip: _Trip, reading: _TripReading, *, stop_ok: bool, stop_msg: str) -> dict[str, Any]:
    """Payload of the ``pump_dry_run`` activity row; a non-JSON alarm value is stored as its repr."""
    alarm_raw = reading.alarm_raw
    return {
        "irrigator_id": trip.irrigator_id,
        "irrigator_name": trip.irrigator_name,
        "cluster_id": trip.cluster_id,
        "alarm_dp": IK10PW_ALARM_DP,
        "alarm_raw": alarm_raw if isinstance(alarm_raw, int | str | bool) else repr(alarm_raw),
        "polls": reading.polls,
        "started_at": reading.started_at,
        "duration_seconds_requested": reading.duration_seconds,
        "elapsed_seconds": reading.elapsed_estimate,
        "stop_ok": stop_ok,
        "stop_message": stop_msg,
    }


# The defaults of the watcher's clock and sleep, bound once at import (as signature defaults were).
_MONOTONIC_CLOCK = time.monotonic
_BLOCKING_SLEEP = time.sleep


class PumpWatcherOptions(TypedDict, total=False):
    """:class:`PumpWatcherService` tuning and collaborators, as keywords (all optional).

    Defaults: the ``PUMP_WATCHER_*`` constants, ``time.monotonic`` / ``time.sleep``, no health
    monitor, never asked to stop.
    """

    poll_seconds: float
    warmup_seconds: float
    max_read_failures: int
    clock: Callable[[], float]
    sleep: Callable[[float], object]
    monitor: DeviceHealthMonitor | None
    stop_requested: Callable[[], bool] | None


class PumpWatcherService:
    """Polls an irrigator's dry-run alarm and stops the pump on trip."""

    def __init__(
        self, repo: IrrigationRepository, registry: DeviceRegistry, **options: Unpack[PumpWatcherOptions]
    ) -> None:
        """Build a watcher.

        ``stop_requested`` lets the server's shutdown cut the watch short: it
        is checked before every poll, and the scheduler wires ``sleep`` to an
        event wait so a shutdown wakes the watcher immediately. On
        interruption the watcher returns ``outcome="interrupted"`` without
        actuating — the caller decides what happens to the still-running pump.
        """
        self._repo = repo
        self._registry = registry
        self._poll = max(0.1, float(options.get("poll_seconds", PUMP_WATCHER_POLL_SECONDS)))
        self._warmup = max(0.0, float(options.get("warmup_seconds", PUMP_WATCHER_WARMUP_SECONDS)))
        self._max_read_failures = max(1, int(options.get("max_read_failures", PUMP_WATCHER_MAX_READ_FAILURES)))
        self._clock = options.get("clock", _MONOTONIC_CLOCK)
        self._sleep = options.get("sleep", _BLOCKING_SLEEP)
        self._monitor = options.get("monitor")
        self._stop_requested = options.get("stop_requested") or (lambda: False)

    def watch(
        self,
        irrigator: Irrigator,
        duration_seconds: int,
        *,
        started_at: int | None = None,
    ) -> WatchOutcome:
        """Poll the dry-run alarm for the duration of an active irrigation.

        Args:
            irrigator: Irrigator running the pump.
            duration_seconds: Requested irrigation duration; watch exits
                ``duration_seconds`` after the deadline begins ticking. A
                negative value is treated as 0.
            started_at: Unix timestamp of the start event, used for the
                aborted-event row and alert payload. Defaults to ``now``.

        Returns:
            A dict describing the outcome: ``{"outcome": "completed"
            | "tripped" | "abandoned" | "interrupted", "polls": int,
            "read_failures": int, "alarm_raw": ..., "elapsed_seconds": float}``.
            ``interrupted`` means ``stop_requested`` fired (server shutdown)
            before the cycle ended; the pump was NOT touched.
        """
        started_at = started_at if started_at is not None else int(time.time())
        duration_seconds = max(0, duration_seconds)  # nothing to watch: complete at once, 0 s elapsed
        deadline = self._clock() + float(duration_seconds)
        warmup_until = self._clock() + self._warmup
        polls = 0
        failures = 0  # consecutive failed reads
        while True:
            if self._stop_requested():
                elapsed = self._elapsed(deadline, duration_seconds)
                return _outcome("interrupted", polls=polls, failures=failures, alarm_raw=None, elapsed=elapsed)
            now = self._clock()
            if now >= deadline:
                elapsed = now - (deadline - duration_seconds)
                return _outcome("completed", polls=polls, failures=0, alarm_raw=None, elapsed=elapsed)

            polls += 1
            state, failure_msg = self._poll_step(irrigator)
            failures = failures + 1 if failure_msg is not None else 0
            if failure_msg is not None and failures >= self._max_read_failures:
                self._log_abandoned(irrigator, failures, failure_msg)
                elapsed = self._elapsed(deadline, duration_seconds)
                return _outcome("abandoned", polls=polls, failures=failures, alarm_raw=None, elapsed=elapsed)
            # Only trip after the warm-up window has elapsed. A pump that's still priming naturally
            # draws lower current and can briefly set the bit before water reaches the impeller.
            if failure_msg is None and HealthAlarm.NO_WATER in state.alarms and now >= warmup_until:
                alarm_raw = self._handle_trip(
                    irrigator, state, started_at=started_at, polls=polls, duration_seconds=duration_seconds
                )
                elapsed = self._elapsed(deadline, duration_seconds)
                return _outcome("tripped", polls=polls, failures=0, alarm_raw=alarm_raw, elapsed=elapsed)
            self._sleep(self._poll)

    # ── Internals ─────────────────────────────────────────────────────────

    def _elapsed(self, deadline: float, duration_seconds: int) -> float:
        """Seconds since the watch began, read off the clock now (``deadline`` minus the duration)."""
        return self._clock() - (deadline - duration_seconds)

    def _poll_step(self, irrigator: Irrigator) -> tuple["DeviceHealthState", str | None]:
        """One poll: the health state, plus the failure message when the read failed or the device is offline."""
        state = self._read_health(irrigator)
        read_error = state.raw.get("error") if isinstance(state.raw, dict) else None
        if read_error or state.offline:
            return state, read_error or "device offline"
        return state, None

    @staticmethod
    def _log_abandoned(irrigator: Irrigator, consecutive_failures: int, last_failure_msg: str) -> None:
        """Say loudly that the irrigation now runs without dry-run protection."""
        logger.warning(
            "Pump watcher abandoning irrigator %d after %d read failures (last: %s) — irrigation continues unprotected",
            irrigator.id,
            consecutive_failures,
            last_failure_msg,
        )

    def _read_health(self, irrigator: Irrigator) -> "DeviceHealthState":
        """Read the device's health surface via the registry-resolved adapter.

        Watcher and slow-path monitor share this code path so a single
        adapter implementation drives both safety surfaces.
        """
        adapter = self._registry.get_irrigator(irrigator)
        return adapter.read_health(irrigator)

    def _handle_trip(
        self,
        irrigator: Irrigator,
        state: "DeviceHealthState",
        *,
        started_at: int,
        polls: int,
        duration_seconds: int,
    ) -> object:
        """Stop the pump, then record the event, the activity and the alert — each durable on its own.

        Stopping the pump is the top priority and always comes first. Each record is then its own
        failure domain (S5): it is written and committed alone, and a failed write or commit is
        logged and rolled back before the next step, so one failure neither poisons the session
        for the others (``PendingRollbackError``) nor undoes what already committed. Nothing in
        the record path raises. The alert itself is raised by :meth:`DeviceHealthMonitor.record`,
        which uses the unified ``health:irrigator:{id}:no_water`` dedup_key. Returns the alarm DP value.
        """
        cluster_id = irrigator.cluster_id
        stop_ok, stop_msg = self._stop_pump(irrigator)
        # Read once, right after the stop: a rollback below expires the instance, and the
        # failure logs must not need the session that just failed.
        trip = _Trip(irrigator.id, irrigator.name, cluster_id)

        alarm_raw = state.raw.get("alarm_raw") if isinstance(state.raw, dict) else None
        logger.critical(
            "Pump dry-run detected on irrigator %d (cluster %d) after %d polls: alarm_raw=%r, stop_ok=%s, stop_msg=%s",
            trip.irrigator_id,
            cluster_id,
            polls,
            alarm_raw,
            stop_ok,
            stop_msg,
        )

        elapsed_estimate = int(time.time()) - started_at
        reading = _TripReading(alarm_raw, polls, started_at, duration_seconds, elapsed_estimate)
        payload = _trip_payload(trip, reading, stop_ok=stop_ok, stop_msg=stop_msg)
        notes = f"pump dry-run detected after ~{elapsed_estimate}s (DP 105={alarm_raw!r}); stop_ok={stop_ok}"
        self._durably("log aborted irrigation event", trip, lambda: self._log_aborted_event(trip, notes))
        self._durably(
            "log the pump dry-run activity",
            trip,
            lambda: self._log_trip_activity(trip, elapsed_estimate=elapsed_estimate, payload=payload),
        )
        if not self._durably(
            "record dry-run state into health monitor", trip, lambda: self._record_trip_state(trip, state)
        ):
            self._requeue_alert(trip)
        return alarm_raw

    def _stop_pump(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Stop the pump — the top-priority trip step; a failure is logged and reported, never raised."""
        stop_ok = False
        stop_msg = ""
        try:
            adapter = self._registry.get_irrigator(irrigator)
            stop_ok, stop_msg = adapter.stop(irrigator)
        except Exception as exc:
            stop_msg = f"adapter.stop raised: {exc}"
            logger.exception("Pump watcher could not stop irrigator %d", irrigator.id)
        return stop_ok, stop_msg

    def _durably(self, what: str, trip: "_Trip", write: Callable[[], object]) -> bool:
        """Run one trip record and commit it; on failure log, roll back and report False — never raise.

        The watcher owns its job session, so each record commits as soon as it is written: the
        pump is already stopped and the trip must be on file even if a later record fails.
        """
        try:
            write()
            self._repo.commit()
        except Exception:
            logger.exception("Failed to %s for irrigator %d", what, trip.irrigator_id)
            self._rollback_quietly()
            return False
        return True

    def _rollback_quietly(self) -> None:
        """Roll back after a failed record so the next one starts clean; a failing rollback is only logged."""
        try:
            self._repo.rollback()
        except Exception:
            logger.warning("Rollback after a failed pump dry-run record failed too", exc_info=True)

    def _log_aborted_event(self, trip: "_Trip", notes: str) -> None:
        """Record the ``aborted`` irrigation event."""
        self._repo.add_irrigation_event(
            irrigator_id=trip.irrigator_id,
            action=EVENT_ACTION_ABORTED,
            duration_minutes=0,
            triggered_by=TRIGGERED_BY_PUMP_WATCHER,
            notes=notes,
        )

    def _log_trip_activity(self, trip: "_Trip", *, elapsed_estimate: int, payload: dict[str, Any]) -> None:
        """Record the critical ``pump_dry_run`` activity row."""
        self._repo.add_activity_event(
            source=SOURCE_PUMP,
            entity_type=ENTITY_IRRIGATOR,
            entity_id=trip.irrigator_id,
            code=ACTIVITY_CODE,
            message=f"Pump dry-run detected on '{trip.irrigator_name}' after ~{elapsed_estimate}s — irrigation aborted",
            severity="critical",
            payload=payload,
        )

    def _record_trip_state(self, trip: "_Trip", state: "DeviceHealthState") -> None:
        """Hand the NO_WATER state to the health monitor, which raises the alert."""
        # Route through the monitor so the inbox + cache + slow-path
        # observer all see the same NO_WATER transition. The monitor owns
        # the unified ``health:irrigator:{id}:no_water`` dedup_key.
        (self._monitor or self._lazy_monitor()).record(
            ENTITY_IRRIGATOR,
            trip.irrigator_id,
            state,
            label=trip.irrigator_name,
            cluster_id=trip.cluster_id,
        )

    def _requeue_alert(self, trip: "_Trip") -> None:
        """The alert's transaction was lost: keep NO_WATER blocking and have the monitor raise it again."""
        monitor = self._monitor
        if monitor is None:
            return
        try:
            monitor.mark_unraised(ENTITY_IRRIGATOR, trip.irrigator_id, HealthAlarm.NO_WATER)
        except Exception:
            logger.exception("Could not requeue the dry-run alert for irrigator %d", trip.irrigator_id)

    def _lazy_monitor(self) -> DeviceHealthMonitor:
        """Build a transient monitor when one wasn't injected.

        Test harness path: callers that don't pass a monitor get a no-op
        write through ``upsert_alert`` keyed on
        ``health:irrigator:{id}:no_water``. The slow-path scheduler still
        owns the long-lived cache; this transient instance only writes the
        alert row and exits.
        """
        return DeviceHealthMonitor(repo=self._repo, registry=self._registry)
