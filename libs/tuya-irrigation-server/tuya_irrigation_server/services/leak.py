"""Leak / stuck-valve detection service.

Runs 30 minutes after a start event. If any sensor in the cluster still shows
rising or pinned-high soil moisture, raises a critical alert and logs a
cooldown anchor event so the decision engine skips the cluster for 24 h.
"""

import logging
import time

from tuya_irrigation_core.models import Alert
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_server.services.alerts import SOURCE_LEAK, raise_alert

logger = logging.getLogger(__name__)

# Soil moisture threshold above which a reading is considered "pinned high".
_PINNED_THRESHOLD = 95.0
# Rising delta: avg(after) > avg(before) + this → sensor never settled.
_RISING_DELTA = 30.0


class LeakDetectionService:
    """Post-irrigation leak and stuck-valve detector."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def check_after_irrigation(self, cluster_id: int, started_at: int) -> list[Alert]:
        """Raise critical alerts when a sensor shows signs of a leak or stuck valve.

        Examines each sensor in the cluster with a 10-minute before-window and a
        30-minute after-window. Detects two conditions:

        - **Pinned**: latest after-window reading exceeds 95% soil moisture.
        - **Still rising**: avg after-window moisture > avg before-window + 30 pp.

        When either condition fires, a critical ``leak_or_stuck_valve`` alert is
        raised and a ``schedule_updated`` event is logged for every irrigator in
        the cluster so the decision engine's 6 h cooldown covers the next 24 h.

        Args:
            cluster_id: Cluster that was irrigated.
            started_at: Unix timestamp of the start event.

        Returns:
            List of Alert rows that were created or refreshed.
        """
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        irrigators = self._repo.get_irrigators_in_cluster(cluster_id)
        alerts: list[Alert] = []

        for sensor in sensors:
            before_readings, after_readings = self._repo.get_readings_around(
                sensor.id,
                started_at,
                before_seconds=600,
                after_seconds=1800,
            )

            after_moisture = [r.soil_moisture for r in after_readings if r.soil_moisture is not None]
            if not after_moisture:
                continue

            before_moisture = [r.soil_moisture for r in before_readings if r.soil_moisture is not None]

            latest_after = after_moisture[-1]
            pinned = latest_after > _PINNED_THRESHOLD

            avg_before = sum(before_moisture) / len(before_moisture) if before_moisture else 0.0
            avg_after = sum(after_moisture) / len(after_moisture)
            still_rising = avg_after > avg_before + _RISING_DELTA

            if not (pinned or still_rising):
                continue

            reason = "pinned >95%" if pinned else "still rising after irrigation"
            message = f"{sensor.name}: soil moisture {reason} (latest={latest_after:.1f}%)"
            logger.warning("Leak/stuck-valve detected — cluster %d, sensor %d: %s", cluster_id, sensor.id, reason)

            alert = raise_alert(
                self._repo,
                source=SOURCE_LEAK,
                code="leak_or_stuck_valve",
                severity="critical",
                title="Possible leak or stuck valve detected",
                message=message,
                cluster_id=cluster_id,
                sensor_id=sensor.id,
                payload={
                    "sensor_id": sensor.id,
                    "sensor_name": sensor.name,
                    "latest_moisture": latest_after,
                    "avg_before": avg_before,
                    "avg_after": avg_after,
                    "reason": reason,
                    "started_at": started_at,
                },
            )
            alerts.append(alert)

        if alerts:
            # Log a cooldown anchor for every irrigator so the decision engine's
            # existing cooldown window treats this cluster as recently irrigated.
            now = int(time.time())
            for irrigator in irrigators:
                self._repo.add_irrigation_event(
                    irrigator_id=irrigator.id,
                    action="schedule_updated",
                    triggered_by="leak_detector",
                    duration_minutes=0,
                    notes="auto-cancel 24h: possible leak or stuck valve",
                    timestamp=now,
                )

        return alerts
