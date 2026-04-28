"""Sensor anomaly detection service.

Two detectors run across all sensors on a rolling window of the last 50
readings:

- **Stale**: if (now − latest_timestamp) > 2 × median sample interval, the
  sensor has gone silent and raises a ``sensor_stale`` warning.
- **Drift/spike**: if |z-score| of the latest soil-moisture reading > 4
  relative to the rolling mean + std of the 50-reading window, raises a
  ``sensor_drift`` warning.

Both emit into the shared alert inbox via ``raise_alert``.
"""

import logging
import statistics
import time

from tuya_irrigation_core.models import Alert
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_server.services.alerts import SOURCE_ANOMALY, raise_alert

logger = logging.getLogger(__name__)

_MIN_READINGS = 10
_WINDOW = 50
_Z_THRESHOLD = 4.0
_STALE_MULTIPLIER = 2.0
# Minimum std to use for z-score; prevents false alarms on near-constant series
# while still catching large absolute deviations (e.g. 95% vs 50% baseline).
_MIN_STD = 1.0


def _median_interval(timestamps: list[int]) -> float | None:
    """Return median gap between consecutive timestamps in seconds."""
    if len(timestamps) < 2:
        return None
    gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    return statistics.median(gaps)


class SensorAnomalyService:
    """Rolling z-score and max-gap anomaly detector for all sensors."""

    def __init__(self, repo: IrrigationRepository):
        self._repo = repo

    def scan(self) -> list[Alert]:
        """Detect stale and drifting sensors across the entire fleet.

        For each sensor with at least 10 readings in the last 72 hours:

        - **Stale**: ``(now - latest_ts) > 2 × median_interval``.
        - **Drift**: ``|z-score of latest soil_moisture| > 4`` versus the
          rolling mean and std of the last 50 soil-moisture readings.
          A minimum std of 1.0 % is applied to avoid false alarms on near-constant
          series where tiny rounding differences would appear as infinite outliers.

        Returns:
            List of Alert rows that were upserted.
        """
        sensors = self._repo.list_all_sensors()
        now = int(time.time())
        alerts: list[Alert] = []

        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=72)
            # get_recent_readings returns DESC — take the most recent _WINDOW
            window = readings[:_WINDOW]

            if len(window) < _MIN_READINGS:
                continue

            # Ascending timestamps for interval computation
            timestamps_asc = sorted(r.timestamp for r in window)
            median_interval = _median_interval(timestamps_asc)

            # ── Stale check ──────────────────────────────────────────────────
            latest_ts = timestamps_asc[-1]
            if median_interval and (now - latest_ts) > _STALE_MULTIPLIER * median_interval:
                gap_seconds = now - latest_ts
                alert = raise_alert(
                    self._repo,
                    source=SOURCE_ANOMALY,
                    code="sensor_stale",
                    severity="warning",
                    title=f"Stale sensor: {sensor.name}",
                    message=(
                        f"{sensor.name}: no reading for {gap_seconds // 60:.0f} min "
                        f"(expected every {median_interval / 60:.0f} min)"
                    ),
                    cluster_id=sensor.cluster_id,
                    sensor_id=sensor.id,
                    payload={
                        "sensor_id": sensor.id,
                        "sensor_name": sensor.name,
                        "latest_ts": latest_ts,
                        "gap_seconds": gap_seconds,
                        "median_interval_s": median_interval,
                    },
                )
                alerts.append(alert)
                logger.warning(
                    "Stale sensor %d (%s): silent for %.0fs, median interval %.0fs",
                    sensor.id,
                    sensor.name,
                    gap_seconds,
                    median_interval,
                )

            # ── Z-score drift check ──────────────────────────────────────────
            soil_values = [r.soil_moisture for r in window if r.soil_moisture is not None]
            if len(soil_values) < _MIN_READINGS:
                continue

            # DESC order → index 0 is most recent
            latest_soil = soil_values[0]
            # Exclude the latest from the baseline to avoid self-contamination
            baseline = soil_values[1:]
            if len(baseline) < _MIN_READINGS - 1:
                continue

            mean = statistics.mean(baseline)
            std = max(statistics.pstdev(baseline), _MIN_STD)
            z = (latest_soil - mean) / std

            if abs(z) > _Z_THRESHOLD:
                alert = raise_alert(
                    self._repo,
                    source=SOURCE_ANOMALY,
                    code="sensor_drift",
                    severity="warning",
                    title=f"Sensor spike/drift: {sensor.name}",
                    message=(
                        f"{sensor.name}: soil moisture {latest_soil:.1f}% "
                        f"is a {z:+.1f}σ outlier (mean={mean:.1f}%, std={std:.1f}%)"
                    ),
                    cluster_id=sensor.cluster_id,
                    sensor_id=sensor.id,
                    payload={
                        "sensor_id": sensor.id,
                        "sensor_name": sensor.name,
                        "latest_value": latest_soil,
                        "mean": mean,
                        "std": std,
                        "z": z,
                        "median_interval_s": median_interval,
                    },
                )
                alerts.append(alert)
                logger.warning(
                    "Sensor drift %d (%s): z=%.2f, latest=%.1f%%, mean=%.1f%%",
                    sensor.id,
                    sensor.name,
                    z,
                    latest_soil,
                    mean,
                )

        return alerts
