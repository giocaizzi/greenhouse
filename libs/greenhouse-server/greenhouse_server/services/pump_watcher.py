"""Pump dry-run watcher.

Runs for the duration of an active irrigation. Polls DP 105 (the IK10PW's
water-shortage alarm) over the Tuya local protocol; on the first positive
reading it immediately stops the pump, raises a critical alert, and records
an ``aborted`` irrigation event.

Why polling and not a persistent socket. The local protocol can push DP
changes asynchronously, but Tuya devices only accept one local TCP
connection at a time and the existing actuation path already opens one
transiently; juggling a persistent socket alongside cloud-API commands adds
reconnection logic without a meaningful latency win — the firmware itself
debounces dry-run detection over several seconds, so a 2 s poll is well
inside its own resolution.

False positives are safe (we stop before the pump is damaged); false
negatives are the danger. The signal is motor-current-based and has
documented quirks (a clogged filter mimics a dry pump), so for unattended
deployments a hardware float switch in the reservoir remains the
recommended belt-and-suspenders safeguard.
"""

import logging
import time
from collections.abc import Callable

from greenhouse_core.devices import TuyaDeviceManager
from greenhouse_core.models import ENTITY_IRRIGATOR, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import SOURCE_PUMP, raise_alert

logger = logging.getLogger(__name__)

ALERT_CODE = "pump_dry_run"
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
    ):
        self._repo = repo
        self._dm = dm
        self._poll = max(0.1, float(poll_seconds))
        self._warmup = max(0.0, float(warmup_seconds))
        self._max_read_failures = max(1, int(max_read_failures))
        self._clock = clock
        self._sleep = sleep

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
            reading = self._dm.read_irrigator_alarm(irrigator)

            if reading.get("error"):
                consecutive_failures += 1
                last_failure_msg = reading.get("error")
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
                if reading.get("no_water") and now >= warmup_until:
                    self._handle_trip(
                        irrigator=irrigator,
                        cluster_id=cluster_id,
                        reading=reading,
                        started_at=started_at,
                        polls=polls,
                        duration_seconds=duration_seconds,
                    )
                    return {
                        "outcome": "tripped",
                        "polls": polls,
                        "read_failures": 0,
                        "alarm_raw": reading.get("alarm_raw"),
                        "elapsed_seconds": self._clock() - (deadline - duration_seconds),
                    }

            self._sleep(self._poll)

    def _handle_trip(
        self,
        *,
        irrigator: Irrigator,
        cluster_id: int,
        reading: dict,
        started_at: int,
        polls: int,
        duration_seconds: int,
    ) -> None:
        """Stop the pump, raise the alert, and record the abort.

        Best-effort by design: each side effect is wrapped so a failure in
        one does not block the others. Stopping the pump is the top priority
        — if the alert or event logging fails, the pump is still off.
        """
        stop_ok = False
        stop_msg = ""
        try:
            stop_ok, stop_msg = self._dm.irrigator_off(irrigator)
        except Exception as exc:
            stop_msg = f"irrigator_off raised: {exc}"
            logger.exception("Pump watcher could not stop irrigator %d", irrigator.id)

        logger.critical(
            "Pump dry-run detected on irrigator %d (cluster %d) after %d polls: alarm_raw=%r, stop_ok=%s, stop_msg=%s",
            irrigator.id,
            cluster_id,
            polls,
            reading.get("alarm_raw"),
            stop_ok,
            stop_msg,
        )

        alarm_raw = reading.get("alarm_raw")
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

        try:
            raise_alert(
                self._repo,
                source=SOURCE_PUMP,
                code=ALERT_CODE,
                severity="critical",
                title=f"Pump dry-run · {irrigator.name}",
                message=(
                    f"Irrigator '{irrigator.name}' reported water shortage "
                    f"(DP 105={alarm_raw!r}) after ~{elapsed_estimate}s. "
                    "Pump stopped to prevent damage; refill the reservoir before resuming."
                ),
                cluster_id=cluster_id,
                payload=payload,
            )
        except Exception:
            logger.exception("Failed to raise pump dry-run alert for irrigator %d", irrigator.id)

        try:
            self._repo.session.commit()
        except Exception:
            logger.exception("Failed to commit pump dry-run side effects for irrigator %d", irrigator.id)
            try:
                self._repo.session.rollback()
            except Exception:
                pass
