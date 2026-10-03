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
from typing import TYPE_CHECKING

from greenhouse_core.constants import (
    ANOMALY_MIN_READINGS,
    ANOMALY_MIN_STD,
    ANOMALY_STALE_INTERVAL_MULTIPLIER,
    ANOMALY_WINDOW_READINGS,
    ANOMALY_Z_THRESHOLD,
)
from greenhouse_core.models import SOURCE_ANOMALY, Alert
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import raise_alert
from greenhouse_server.services.notify import NtfyClient

if TYPE_CHECKING:
    from greenhouse_core.models import Sensor, SensorReading

logger = logging.getLogger(__name__)


def _median_interval(timestamps: list[int]) -> float | None:
    """Return median gap between consecutive timestamps in seconds."""
    if len(timestamps) < 2:
        return None
    gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    return statistics.median(gaps)


def _latest_soil_zscore(window: "list[SensorReading]") -> tuple[float, float, float, float] | None:
    """(latest, mean, std, z) of the newest soil reading against the rest; None with too few readings."""
    soil_values = [r.soil_moisture for r in window if r.soil_moisture is not None]
    if len(soil_values) < ANOMALY_MIN_READINGS:
        return None

    # DESC order → index 0 is most recent
    latest_soil = soil_values[0]
    # Exclude the latest from the baseline to avoid self-contamination
    baseline = soil_values[1:]
    if len(baseline) < ANOMALY_MIN_READINGS - 1:
        return None

    mean = statistics.mean(baseline)
    std = max(statistics.pstdev(baseline), ANOMALY_MIN_STD)
    z = (latest_soil - mean) / std
    return latest_soil, mean, std, z


class SensorAnomalyService:
    """Rolling z-score and max-gap anomaly detector for all sensors."""

    def __init__(self, repo: IrrigationRepository, notifier: NtfyClient | None = None):
        self._repo = repo
        self._notifier = notifier

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
            # get_recent_readings returns DESC — take the most recent ANOMALY_WINDOW_READINGS
            window = readings[:ANOMALY_WINDOW_READINGS]

            if len(window) < ANOMALY_MIN_READINGS:
                continue

            # Ascending timestamps for interval computation
            timestamps_asc = sorted(r.timestamp for r in window)
            median_interval = _median_interval(timestamps_asc)

            # The stale check runs before the drift check, for every sensor.
            stale = self._stale_alert(sensor, timestamps_asc, median_interval, now)
            if stale is not None:
                alerts.append(stale)
            drift = self._drift_alert(sensor, window, median_interval)
            if drift is not None:
                alerts.append(drift)

        return alerts

    def _stale_alert(
        self, sensor: "Sensor", timestamps_asc: list[int], median_interval: float | None, now: int
    ) -> Alert | None:
        """Raise ``sensor_stale`` when the sensor has been silent for over twice its usual interval."""
        latest_ts = timestamps_asc[-1]
        if median_interval and (now - latest_ts) > ANOMALY_STALE_INTERVAL_MULTIPLIER * median_interval:
            gap_seconds = now - latest_ts
            alert = raise_alert(
                self._repo,
                notifier=self._notifier,
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
            logger.warning(
                "Stale sensor %d (%s): silent for %.0fs, median interval %.0fs",
                sensor.id,
                sensor.name,
                gap_seconds,
                median_interval,
            )
            return alert
        return None

    def _drift_alert(
        self, sensor: "Sensor", window: "list[SensorReading]", median_interval: float | None
    ) -> Alert | None:
        """Raise ``sensor_drift`` when the latest soil reading is a > 4σ outlier against the window."""
        stats = _latest_soil_zscore(window)
        if stats is None:
            return None
        latest_soil, mean, std, z = stats

        if abs(z) > ANOMALY_Z_THRESHOLD:
            alert = raise_alert(
                self._repo,
                notifier=self._notifier,
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
            logger.warning(
                "Sensor drift %d (%s): z=%.2f, latest=%.1f%%, mean=%.1f%%",
                sensor.id,
                sensor.name,
                z,
                latest_soil,
                mean,
            )
            return alert
        return None
