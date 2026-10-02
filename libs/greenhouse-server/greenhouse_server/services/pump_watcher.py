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

Why we record through the monitor. PR 1.5 unified the slow ambient
observer and the fast in-flight watchdog onto a single dedup_key scheme
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
from typing import TYPE_CHECKING, Any, TypedDict

from greenhouse_core.constants import (
    PUMP_WATCHER_MAX_READ_FAILURES,
    PUMP_WATCHER_POLL_SECONDS,
    PUMP_WATCHER_WARMUP_SECONDS,
)
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import HealthAlarm
from greenhouse_core.models import ENTITY_IRRIGATOR, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import DeviceHealthMonitor

if TYPE_CHECKING:
    from greenhouse_core.devices.health import DeviceHealthState

logger = logging.getLogger(__name__)

# The watcher's externally-visible alert code now mirrors the canonical
# health alarm so the inbox has one row per condition. Tests + integrations
# can keep importing ``ALERT_CODE`` from this module.
ALERT_CODE = HealthAlarm.NO_WATER.value  # "no_water"
EVENT_ACTION_ABORTED = "aborted"
ACTIVITY_CODE = "pump_dry_run"


class WatchOutcome(TypedDict):
    """Result of one watch; the key order is the order callers and logs see."""

    outcome: str
    polls: int
    read_failures: int
    alarm_raw: Any
    elapsed_seconds: float


def _outcome(kind: str, *, polls: int, failures: int, alarm_raw: Any, elapsed: float) -> WatchOutcome:
    """Build a watch result; the caller reads the clock for elapsed at its own exit point."""
    return {
        "outcome": kind,
        "polls": polls,
        "read_failures": failures,
        "alarm_raw": alarm_raw,
        "elapsed_seconds": elapsed,
    }


class PumpWatcherService:
    """Polls an irrigator's dry-run alarm and stops the pump on trip."""

    def __init__(
        self,
        repo: IrrigationRepository,
        registry: DeviceRegistry,
        *,
        poll_seconds: float = PUMP_WATCHER_POLL_SECONDS,
        warmup_seconds: float = PUMP_WATCHER_WARMUP_SECONDS,
        max_read_failures: int = PUMP_WATCHER_MAX_READ_FAILURES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        monitor: DeviceHealthMonitor | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ):
        """Build a watcher.

        ``stop_requested`` lets the server's shutdown cut the watch short: it
        is checked before every poll, and the scheduler wires ``sleep`` to an
        event wait so a shutdown wakes the watcher immediately. On
        interruption the watcher returns ``outcome="interrupted"`` without
        actuating — the caller decides what happens to the still-running pump.
        """
        self._repo = repo
        self._registry = registry
        self._poll = max(0.1, float(poll_seconds))
        self._warmup = max(0.0, float(warmup_seconds))
        self._max_read_failures = max(1, int(max_read_failures))
        self._clock = clock
        self._sleep = sleep
        self._monitor = monitor
        self._stop_requested = stop_requested or (lambda: False)

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
                ``duration_seconds`` after the deadline begins ticking.
            started_at: Unix timestamp of the start event, used for the
                aborted-event row and alert payload. Defaults to ``now``.

        Returns:
            A dict describing the outcome: ``{"outcome": "completed"
            | "tripped" | "abandoned" | "interrupted", "polls": int,
            "read_failures": int, "alarm_raw": ..., "elapsed_seconds": float}``.
            ``interrupted`` means ``stop_requested`` fired (server shutdown)
            before the cycle ended; the pump was NOT touched.
        """
        cluster_id = irrigator.cluster_id
        started_at = started_at if started_at is not None else int(time.time())
        deadline = self._clock() + max(0.0, float(duration_seconds))
        warmup_until = self._clock() + self._warmup
        polls = 0
        failures = 0  # consecutive failed reads
        while True:
            if self._stop_requested():
                elapsed = self._clock() - (deadline - duration_seconds)
                return _outcome("interrupted", polls=polls, failures=failures, alarm_raw=None, elapsed=elapsed)
            now = self._clock()
            if now >= deadline:
                elapsed = now - (deadline - duration_seconds)
                return _outcome("completed", polls=polls, failures=0, alarm_raw=None, elapsed=elapsed)

            polls += 1
            state, failure_msg = self._poll_step(irrigator)
            if failure_msg is not None:
                failures += 1
                if failures >= self._max_read_failures:
                    self._log_abandoned(irrigator, failures, failure_msg)
                    elapsed = self._clock() - (deadline - duration_seconds)
                    return _outcome("abandoned", polls=polls, failures=failures, alarm_raw=None, elapsed=elapsed)
            else:
                failures = 0
                # Only trip after the warm-up window has elapsed. A pump that's still priming naturally
                # draws lower current and can briefly set the bit before water reaches the impeller.
                if HealthAlarm.NO_WATER in state.alarms and now >= warmup_until:
                    self._handle_trip(
                        irrigator=irrigator,
                        cluster_id=cluster_id,
                        state=state,
                        started_at=started_at,
                        polls=polls,
                        duration_seconds=duration_seconds,
                    )
                    alarm_raw = state.raw.get("alarm_raw") if isinstance(state.raw, dict) else None
                    elapsed = self._clock() - (deadline - duration_seconds)
                    return _outcome("tripped", polls=polls, failures=0, alarm_raw=alarm_raw, elapsed=elapsed)
            self._sleep(self._poll)

    # ── Internals ─────────────────────────────────────────────────────────

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
        *,
        irrigator: Irrigator,
        cluster_id: int,
        state: "DeviceHealthState",
        started_at: int,
        polls: int,
        duration_seconds: int,
    ) -> None:
        """Stop the pump, record activity / event, and route the alert through the monitor.

        Best-effort by design: each side effect is wrapped so a failure in
        one does not block the others. Stopping the pump is the top priority
        — if the activity log or monitor record fails, the pump is still off.
        The alert itself is raised by :meth:`DeviceHealthMonitor.record`,
        which uses the unified ``health:irrigator:{id}:no_water`` dedup_key.
        """
        from greenhouse_server.services.alerts import SOURCE_PUMP

        stop_ok = False
        stop_msg = ""
        try:
            adapter = self._registry.get_irrigator(irrigator)
            stop_ok, stop_msg = adapter.stop(irrigator)
        except Exception as exc:
            stop_msg = f"adapter.stop raised: {exc}"
            logger.exception("Pump watcher could not stop irrigator %d", irrigator.id)

        alarm_raw = state.raw.get("alarm_raw") if isinstance(state.raw, dict) else None
        logger.critical(
            "Pump dry-run detected on irrigator %d (cluster %d) after %d polls: alarm_raw=%r, stop_ok=%s, stop_msg=%s",
            irrigator.id,
            cluster_id,
            polls,
            alarm_raw,
            stop_ok,
            stop_msg,
        )

        elapsed_estimate = int(time.time()) - started_at
        payload = {
            "irrigator_id": irrigator.id,
            "irrigator_name": irrigator.name,
            "cluster_id": cluster_id,
            "alarm_dp": 105,
            "alarm_raw": alarm_raw if isinstance(alarm_raw, int | str | bool) else repr(alarm_raw),
            "polls": polls,
            "started_at": started_at,
            "duration_seconds_requested": duration_seconds,
            "elapsed_seconds": elapsed_estimate,
            "stop_ok": stop_ok,
            "stop_message": stop_msg,
        }

        try:
            self._repo.add_irrigation_event(
                irrigator_id=irrigator.id,
                action=EVENT_ACTION_ABORTED,
                duration_minutes=0,
                triggered_by="pump_watcher",
                notes=(f"pump dry-run detected after ~{elapsed_estimate}s (DP 105={alarm_raw!r}); stop_ok={stop_ok}"),
            )
        except Exception:
            logger.exception("Failed to log aborted irrigation event for irrigator %d", irrigator.id)

        try:
            self._repo.add_activity_event(
                source=SOURCE_PUMP,
                entity_type=ENTITY_IRRIGATOR,
                entity_id=irrigator.id,
                code=ACTIVITY_CODE,
                message=(
                    f"Pump dry-run detected on '{irrigator.name}' after ~{elapsed_estimate}s — irrigation aborted"
                ),
                severity="critical",
                payload=payload,
            )
        except Exception:
            logger.exception("Failed to log activity event for pump dry-run on irrigator %d", irrigator.id)

        # Route through the monitor so the inbox + cache + slow-path
        # observer all see the same NO_WATER transition. The monitor owns
        # the unified ``health:irrigator:{id}:no_water`` dedup_key.
        try:
            monitor = self._monitor or self._lazy_monitor()
            if monitor is not None:
                monitor.record(
                    ENTITY_IRRIGATOR,
                    irrigator.id,
                    state,
                    label=irrigator.name,
                    cluster_id=cluster_id,
                )
        except Exception:
            logger.exception("Failed to record dry-run state into health monitor for irrigator %d", irrigator.id)

        try:
            self._repo.session.commit()
        except Exception:
            logger.exception("Failed to commit pump dry-run side effects for irrigator %d", irrigator.id)
            try:
                self._repo.session.rollback()
            except Exception:
                pass

    def _lazy_monitor(self) -> DeviceHealthMonitor | None:
        """Build a transient monitor when one wasn't injected.

        Test harness path: callers that don't pass a monitor get a no-op
        write through ``upsert_alert`` keyed on
        ``health:irrigator:{id}:no_water``. The slow-path scheduler still
        owns the long-lived cache; this transient instance only writes the
        alert row and exits.
        """
        return DeviceHealthMonitor(repo=self._repo, registry=self._registry)
