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

from greenhouse_core.devices import DeviceRegistry, TuyaDeviceManager
from greenhouse_core.devices.health import HealthAlarm
from greenhouse_core.models import ENTITY_IRRIGATOR, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import DeviceHealthMonitor

logger = logging.getLogger(__name__)

# The watcher's externally-visible alert code now mirrors the canonical
# health alarm so the inbox has one row per condition. Tests + integrations
# can keep importing ``ALERT_CODE`` from this module.
ALERT_CODE = HealthAlarm.NO_WATER.value  # "no_water"
EVENT_ACTION_ABORTED = "aborted"
ACTIVITY_CODE = "pump_dry_run"


class PumpWatcherService:
    """Polls an irrigator's dry-run alarm and stops the pump on trip."""

    def __init__(
        self,
        repo: IrrigationRepository,
        dm: TuyaDeviceManager,
        *,
        poll_seconds: float = 2.0,
        warmup_seconds: float = 5.0,
        max_read_failures: int = 5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        registry: DeviceRegistry | None = None,
        monitor: DeviceHealthMonitor | None = None,
    ):
        self._repo = repo
        self._dm = dm
        self._poll = max(0.1, float(poll_seconds))
        self._warmup = max(0.0, float(warmup_seconds))
        self._max_read_failures = max(1, int(max_read_failures))
        self._clock = clock
        self._sleep = sleep
        self._registry = registry
        self._monitor = monitor

    def watch(
        self,
        irrigator: Irrigator,
        duration_seconds: int,
        *,
        started_at: int | None = None,
    ) -> dict:
        """Poll the dry-run alarm for the duration of an active irrigation.

        Args:
            irrigator: Irrigator running the pump.
            duration_seconds: Requested irrigation duration; watch exits
                ``duration_seconds`` after the deadline begins ticking.
            started_at: Unix timestamp of the start event, used for the
                aborted-event row and alert payload. Defaults to ``now``.

        Returns:
            A dict describing the outcome: ``{"outcome": "completed"
            | "tripped" | "abandoned", "polls": int, "read_failures": int,
            "alarm_raw": ..., "elapsed_seconds": float}``.
        """
        cluster_id = irrigator.cluster_id
        started_at = started_at if started_at is not None else int(time.time())
        deadline = self._clock() + max(0.0, float(duration_seconds))
        warmup_until = self._clock() + self._warmup

        polls = 0
        consecutive_failures = 0
        last_failure_msg: str | None = None

        while True:
            now = self._clock()
            if now >= deadline:
                return {
                    "outcome": "completed",
                    "polls": polls,
                    "read_failures": 0,
                    "alarm_raw": None,
                    "elapsed_seconds": now - (deadline - duration_seconds),
                }

            polls += 1
            state = self._read_health(irrigator)
            read_error = state.raw.get("error") if isinstance(state.raw, dict) else None

            if read_error or state.offline:
                consecutive_failures += 1
                last_failure_msg = read_error or "device offline"
                if consecutive_failures >= self._max_read_failures:
                    logger.warning(
                        "Pump watcher abandoning irrigator %d after %d read failures "
                        "(last: %s) — irrigation continues unprotected",
                        irrigator.id,
                        consecutive_failures,
                        last_failure_msg,
                    )
                    return {
                        "outcome": "abandoned",
                        "polls": polls,
                        "read_failures": consecutive_failures,
                        "alarm_raw": None,
                        "elapsed_seconds": self._clock() - (deadline - duration_seconds),
                    }
            else:
                consecutive_failures = 0
                # Only trip after the warm-up window has elapsed. A pump that's
                # still priming naturally draws lower current and can briefly
                # set the bit before water reaches the impeller.
                if HealthAlarm.NO_WATER in state.alarms and now >= warmup_until:
                    self._handle_trip(
                        irrigator=irrigator,
                        cluster_id=cluster_id,
                        state=state,
                        started_at=started_at,
                        polls=polls,
                        duration_seconds=duration_seconds,
                    )
                    return {
                        "outcome": "tripped",
                        "polls": polls,
                        "read_failures": 0,
                        "alarm_raw": state.raw.get("alarm_raw") if isinstance(state.raw, dict) else None,
                        "elapsed_seconds": self._clock() - (deadline - duration_seconds),
                    }

            self._sleep(self._poll)

    # ── Internals ─────────────────────────────────────────────────────────

    def _read_health(self, irrigator: Irrigator):
        """Read the device's health surface.

        Prefers the registry-backed adapter when available so the watcher
        and the slow-path monitor share one code path. Falls back to the
        legacy ``TuyaDeviceManager`` shim — tests inject a MagicMock dm
        whose ``read_irrigator_health`` returns a canned state.
        """
        if self._registry is not None:
            adapter = self._registry.get_irrigator(irrigator)
            return adapter.read_health(irrigator)
        return self._dm.read_irrigator_health(irrigator)

    def _handle_trip(
        self,
        *,
        irrigator: Irrigator,
        cluster_id: int,
        state,
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
            stop_ok, stop_msg = self._dm.irrigator_off(irrigator)
        except Exception as exc:
            stop_msg = f"irrigator_off raised: {exc}"
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

        Test harness path: callers that don't pass a registry or monitor
        get a no-op write through ``upsert_alert`` keyed on
        ``health:irrigator:{id}:no_water``. The slow-path scheduler still
        owns the long-lived cache; this transient instance only writes
        the alert row and exits.
        """
        if self._registry is None:
            return None
        return DeviceHealthMonitor(repo=self._repo, registry=self._registry)
