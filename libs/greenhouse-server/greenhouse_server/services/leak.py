"""Leak / stuck-valve detection service.

Runs ``LEAK_CHECK_DELAY_SECONDS`` (30 min) after a start event and asks one
question per sensor: **did the soil settle, or is water still arriving?**

A successful irrigation and a stuck valve look identical if you only compare a
"before" average to an "after" average — both raise moisture. What separates
them is the *shape* of the after-window: a dose that landed shows a jump that
plateaus or already drains back inside 30 minutes, while a valve that never
closed shows moisture still climbing at the end of the window, or a probe
pinned at the top of its range.

Three rules follow from that, and all three refuse to fire on thin data
(issue #103 — the previous implementation treated an empty baseline window as
"soil was at 0%", which made every healthy cycle look like a flood):

- **Insufficient data** → no finding. Sensor rows land in SQLite at sync time,
  so short windows are routinely empty; silence is the only safe verdict.
- **Pinned** → the last ``LEAK_PINNED_MIN_SAMPLES`` readings are all above
  ``LEAK_PINNED_THRESHOLD``.
- **Never settled** → moisture is still at its peak at the end of the window,
  climbed across the window, and sits ``LEAK_RISING_DELTA`` above the
  pre-irrigation baseline.

Readings are consumed through the shared *cleaned view*
(``logic/cleaning.py``) exactly like the decision engine, so a single spurious
sample — the kind the anomaly scan already flags as ``sensor_drift`` — cannot
raise a critical alert on its own.

A confirmed finding raises a critical ``leak_or_stuck_valve`` alert, which is
what actually holds the cluster: ``IrrigationLogic._enforce_leak_hold`` skips
automatic irrigation for ``LEAK_HOLD_HOURS`` while that alert is unresolved.
When a later check finds the same sensor settled, the alert is resolved and the
hold lifts with it.
"""

import logging
import statistics
import time

from greenhouse_core.constants import (
    LEAK_AFTER_WINDOW_SECONDS,
    LEAK_ALERT_CODE,
    LEAK_BEFORE_WINDOW_SECONDS,
    LEAK_HOLD_HOURS,
    LEAK_MIN_AFTER_SAMPLES,
    LEAK_MIN_BEFORE_SAMPLES,
    LEAK_PINNED_MIN_SAMPLES,
    LEAK_PINNED_THRESHOLD,
    LEAK_RISING_DELTA,
    LEAK_SETTLE_TOLERANCE,
)
from greenhouse_core.logic.cleaning import clean_readings_around
from greenhouse_core.models import ENTITY_CLUSTER, ENTITY_SENSOR, Alert, Sensor
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import SOURCE_LEAK, raise_alert
from greenhouse_server.services.notify import NtfyClient

logger = logging.getLogger(__name__)


class LeakDetectionService:
    """Post-irrigation leak and stuck-valve detector."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase, notifier: NtfyClient | None = None):
        self._repo = repo
        self._plant_db = plant_db
        self._notifier = notifier

    def check_after_irrigation(self, cluster_id: int, started_at: int) -> list[Alert]:
        """Raise critical alerts when a sensor shows signs of a leak or stuck valve.

        Evaluates every sensor in the cluster against the cleaned view of its
        series around ``started_at``. Each sensor lands in one of three states:

        - **confirmed** — pinned high, or never settled: a critical
          ``leak_or_stuck_valve`` alert is raised (or refreshed), which holds
          the cluster's automatic irrigation for ``LEAK_HOLD_HOURS``.
        - **settled** — the dose behaved: any open leak alert for that sensor
          is resolved, releasing the hold.
        - **inconclusive** — too few samples to judge: nothing is raised and
          nothing is resolved.

        Args:
            cluster_id: Cluster that was irrigated.
            started_at: Unix timestamp of the start event.

        Returns:
            List of Alert rows that were created or refreshed.
        """
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        alerts: list[Alert] = []

        for sensor in sensors:
            verdict = self._evaluate_sensor(sensor, started_at)
            if verdict is None:
                logger.debug(
                    "Leak check inconclusive — cluster %d, sensor %d: not enough readings around %d",
                    cluster_id,
                    sensor.id,
                    started_at,
                )
                continue

            reason, evidence = verdict
            if reason is None:
                self._clear_sensor(cluster_id, sensor)
                continue

            alerts.append(self._raise_for_sensor(cluster_id, sensor, started_at, reason, evidence))

        if alerts:
            now = int(time.time())
            self._repo.add_activity_event(
                source="leak",
                entity_type=ENTITY_CLUSTER,
                entity_id=cluster_id,
                code="leak_hold",
                message=(
                    f"automatic irrigation held for {LEAK_HOLD_HOURS}h: "
                    f"possible leak or stuck valve on {len(alerts)} sensor(s)"
                ),
                severity="critical",
                payload={
                    "started_at": started_at,
                    "hold_until": now + LEAK_HOLD_HOURS * 3600,
                    "sensor_ids": [a.entity_id for a in alerts],
                },
                timestamp=now,
            )

        return alerts

    # ── Detection ─────────────────────────────────────────────────────────

    def _evaluate_sensor(self, sensor: Sensor, started_at: int) -> tuple[str | None, dict] | None:
        """Judge one sensor's behaviour around an irrigation.

        Args:
            sensor: Sensor to evaluate.
            started_at: Unix timestamp of the start event.

        Returns:
            ``None`` when the data is too thin to judge. Otherwise
            ``(reason, evidence)`` where ``reason`` is ``None`` for a settled
            sensor or a short human string naming the rule that fired.
        """
        before_rows, after_rows = self._repo.get_readings_around(
            sensor.id,
            started_at,
            before_seconds=LEAK_BEFORE_WINDOW_SECONDS,
            after_seconds=LEAK_AFTER_WINDOW_SECONDS,
        )
        before_readings, after_readings = clean_readings_around(before_rows, after_rows)

        after = [r.soil_moisture for r in after_readings if r.soil_moisture is not None]
        before = [r.soil_moisture for r in before_readings if r.soil_moisture is not None]

        if len(after) < LEAK_MIN_AFTER_SAMPLES:
            return None

        evidence: dict = {
            "latest_moisture": after[-1],
            "peak_after": max(after),
            "after_samples": len(after),
            "before_samples": len(before),
        }

        # Rule 1 — pinned high. Needs consecutive samples at the top of the
        # range so one glitch reading can't stand in for a flooded pot. This
        # rule needs no baseline: a probe reading >95% for a sustained stretch
        # is wrong regardless of where it started.
        tail = after[-LEAK_PINNED_MIN_SAMPLES:]
        if len(tail) >= LEAK_PINNED_MIN_SAMPLES and all(v > LEAK_PINNED_THRESHOLD for v in tail):
            return f"pinned >{LEAK_PINNED_THRESHOLD:.0f}%", evidence

        # Rule 2 — never settled. Requires a real pre-irrigation baseline; with
        # no baseline there is nothing to compare against, so the verdict is
        # "inconclusive" rather than "settled" — a missing baseline must not
        # release a hold raised by an earlier, better-fed check either.
        if len(before) < LEAK_MIN_BEFORE_SAMPLES:
            return None

        baseline = statistics.median(before)
        evidence["baseline"] = baseline
        first_after, last_after, peak_after = after[0], after[-1], max(after)

        still_climbing = last_after >= peak_after - LEAK_SETTLE_TOLERANCE
        rose_through_window = last_after - first_after > LEAK_SETTLE_TOLERANCE
        far_above_baseline = last_after - baseline >= LEAK_RISING_DELTA

        if still_climbing and rose_through_window and far_above_baseline:
            evidence["rise_over_baseline"] = last_after - baseline
            evidence["rise_in_window"] = last_after - first_after
            return "still rising after irrigation", evidence

        return None, evidence

    # ── Alert lifecycle ───────────────────────────────────────────────────

    def _raise_for_sensor(self, cluster_id: int, sensor: Sensor, started_at: int, reason: str, evidence: dict) -> Alert:
        """Raise (or refresh) the critical alert that holds the cluster."""
        latest = evidence["latest_moisture"]
        message = f"{sensor.name}: soil moisture {reason} (latest={latest:.1f}%)"
        logger.warning("Leak/stuck-valve detected — cluster %d, sensor %d: %s", cluster_id, sensor.id, reason)

        return raise_alert(
            self._repo,
            notifier=self._notifier,
            source=SOURCE_LEAK,
            code=LEAK_ALERT_CODE,
            severity="critical",
            title="Possible leak or stuck valve detected",
            message=message,
            cluster_id=cluster_id,
            sensor_id=sensor.id,
            payload={
                "sensor_id": sensor.id,
                "sensor_name": sensor.name,
                "reason": reason,
                "started_at": started_at,
                "hold_hours": LEAK_HOLD_HOURS,
                **evidence,
            },
        )

    def _clear_sensor(self, cluster_id: int, sensor: Sensor) -> None:
        """Resolve this sensor's open leak alert once its soil behaves again."""
        for alert in self._repo.list_alerts(cluster_id=cluster_id, limit=200):
            if alert.code != LEAK_ALERT_CODE or alert.status == "resolved":
                continue
            if alert.entity_type != ENTITY_SENSOR or alert.entity_id != sensor.id:
                continue
            self._repo.resolve_alert(alert.id)
            logger.info("Leak hold released — cluster %d, sensor %d settled", cluster_id, sensor.id)
