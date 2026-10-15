"""Sensor anomaly detection service.

Three detectors run across all sensors: stale and drift on a rolling window of
the last 50 readings, flatline on everything the 72 h look-back loaded:

- **Stale**: if (now - latest_timestamp) exceeds both 2 x the median sample
  interval and one sync interval + ``SENSOR_OFFLINE_GRACE_MINUTES``, the sensor
  has gone silent and raises a ``sensor_stale`` warning; readings land only when
  the sync job runs, so a healthy sensor looks silent until the next sync. The
  alert resolves on the first scan that finds the sensor fresh again.
- **Drift/spike**: if |z-score| of the latest soil-moisture reading > 4
  relative to the rolling mean + std of the 50-reading window, raises a
  ``sensor_drift`` warning. A **rising step that holds** is a watering (often a
  hand watering nobody logged), not drift: when the newest
  ``ANOMALY_STEP_HOLD_READINGS`` values all sit well above the earlier level, no
  drift is raised and an open ``sensor_drift`` alert resolves. A lone outlier, or
  a drop, is still drift.
- **Flatline**: a fresh sensor whose newest soil readings stay within
  ``SENSOR_FLATLINE_MAX_RANGE`` for ``SENSOR_FLATLINE_HOURS`` with a mean at an
  extreme (<= 2 % or >= 98 %) raises a ``sensor_flatline`` warning (probe out of
  the soil, in water, or faulty); it resolves once the series moves. A stale
  sensor is left to ``sensor_stale``. The opposite blind spot of drift: a flat
  series can never be a z-score outlier.

Every detector reads raw rows (cleaning could mask the pattern) and emits into
the shared alert inbox via ``raise_alert``.
"""

import logging
import statistics
import time
from typing import TYPE_CHECKING

from greenhouse_core.constants import (
    ANOMALY_LOOKBACK_HOURS,
    ANOMALY_MIN_READINGS,
    ANOMALY_MIN_STD,
    ANOMALY_STALE_INTERVAL_MULTIPLIER,
    ANOMALY_STEP_HOLD_READINGS,
    ANOMALY_WINDOW_READINGS,
    ANOMALY_Z_THRESHOLD,
    CLEANING_MAD_SCALE,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    SECONDS_PER_HOUR,
    SENSOR_FLATLINE_HIGH,
    SENSOR_FLATLINE_HOURS,
    SENSOR_FLATLINE_LOW,
    SENSOR_FLATLINE_MAX_RANGE,
    SENSOR_OFFLINE_GRACE_MINUTES,
)
from greenhouse_core.models import SOURCE_ANOMALY, Alert
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import clear_alert, raise_alert
from greenhouse_server.services.notify import NtfyClient

if TYPE_CHECKING:
    from greenhouse_core.models import Sensor, SensorReading

logger = logging.getLogger(__name__)

_MIN_TIMESTAMPS_FOR_GAP = 2  # a gap needs two points


def _median_interval(timestamps: list[int]) -> float | None:
    """Return median gap between consecutive timestamps in seconds."""
    if len(timestamps) < _MIN_TIMESTAMPS_FOR_GAP:
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

    mean = statistics.mean(baseline)
    std = max(statistics.pstdev(baseline), ANOMALY_MIN_STD)
    z = (latest_soil - mean) / std
    return latest_soil, mean, std, z


def _is_rising_step(window: "list[SensorReading]") -> bool:
    """True when the newest soil values all sit > ``ANOMALY_Z_THRESHOLD`` sigmas above the level before them.

    Median and scaled MAD of the earlier readings, so the post-step readings already in
    the window do not drag the baseline up the way a mean and std would.
    """
    soil = [r.soil_moisture for r in window if r.soil_moisture is not None]
    held, before = soil[:ANOMALY_STEP_HOLD_READINGS], soil[ANOMALY_STEP_HOLD_READINGS:]
    if len(held) < ANOMALY_STEP_HOLD_READINGS or not before:
        return False
    level = statistics.median(before)
    spread = max(CLEANING_MAD_SCALE * statistics.median(abs(v - level) for v in before), ANOMALY_MIN_STD)
    return (min(held) - level) / spread > ANOMALY_Z_THRESHOLD


def _flatline(readings: "list[SensorReading]") -> tuple[float, int] | None:
    """(mean, span seconds) of the newest flat soil run when it sits at an extreme long enough; else None.

    ``readings`` is DESC. The run is the newest values whose max - min stays within
    ``SENSOR_FLATLINE_MAX_RANGE``; a watering step ends it, so a moving series never flatlines.
    """
    soil = [(r.timestamp, r.soil_moisture) for r in readings if r.soil_moisture is not None]
    if not soil:
        return None
    newest_ts, first = soil[0]
    run, oldest_ts, low, high = [first], newest_ts, first, first
    for ts, value in soil[1:]:
        low, high = min(low, value), max(high, value)
        if high - low > SENSOR_FLATLINE_MAX_RANGE:
            break
        run.append(value)
        oldest_ts = ts
    level, span = statistics.mean(run), newest_ts - oldest_ts
    at_extreme = level <= SENSOR_FLATLINE_LOW or level >= SENSOR_FLATLINE_HIGH
    return (level, span) if at_extreme and span >= SENSOR_FLATLINE_HOURS * SECONDS_PER_HOUR else None


class SensorAnomalyService:
    """Rolling z-score and max-gap anomaly detector for all sensors."""

    def __init__(
        self,
        repo: IrrigationRepository,
        notifier: NtfyClient | None = None,
        sync_interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES,
    ) -> None:
        """Bind the scan to the caller's repository; ``notifier`` pushes newly opened alerts (``None``: no push).

        ``sync_interval_minutes`` is the sensor sync cadence: readings are persisted only by
        the sync, so a sensor is not stale before one interval plus ``SENSOR_OFFLINE_GRACE_MINUTES``.
        """
        self._repo = repo
        self._notifier = notifier
        self._min_stale_seconds = (sync_interval_minutes + SENSOR_OFFLINE_GRACE_MINUTES) * 60

    def scan(self) -> list[Alert]:
        """Detect stale, flat-lined and drifting sensors across the entire fleet.

        For each sensor with at least 10 readings in the last 72 hours:

        - **Stale**: ``(now - latest_ts) > max(2 * median_interval, sync interval + grace)``;
          a fresh sensor resolves its open ``sensor_stale`` alert.
        - **Flatline** (fresh sensors only): the newest soil readings stay within 1 pp
          for 48 h at <= 2 % or >= 98 % (``SENSOR_FLATLINE_*``); resolves once the series moves.
        - **Drift**: ``|z-score of latest soil_moisture| > 4`` versus the
          rolling mean and std of the last 50 soil-moisture readings.
          A minimum std of 1.0 % is applied to avoid false alarms on near-constant
          series where tiny rounding differences would appear as infinite outliers.
          A rising step held by the newest readings (a watering) is not drift and
          resolves an open ``sensor_drift`` alert.

        Returns:
            List of Alert rows that were upserted.
        """
        sensors = self._repo.list_all_sensors()
        now = int(time.time())
        alerts: list[Alert] = []

        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=ANOMALY_LOOKBACK_HOURS)
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
            else:  # a stale sensor is flat because nothing arrives: sensor_stale says it
                flat = self._flatline_alert(sensor, readings)
                alerts.extend([flat] if flat is not None else [])
            drift = self._drift_unless_step(sensor, window, median_interval)
            if drift is not None:
                alerts.append(drift)

        return alerts

    def _stale_alert(
        self, sensor: "Sensor", timestamps_asc: list[int], median_interval: float | None, now: int
    ) -> Alert | None:
        """Raise ``sensor_stale`` past twice its usual interval and the sync window; resolve it once fresh."""
        latest_ts = timestamps_asc[-1]
        threshold = max(ANOMALY_STALE_INTERVAL_MULTIPLIER * (median_interval or 0), self._min_stale_seconds)
        if median_interval and (now - latest_ts) > threshold:
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
        clear_alert(
            self._repo, source=SOURCE_ANOMALY, code="sensor_stale", cluster_id=sensor.cluster_id, sensor_id=sensor.id
        )
        return None

    def _flatline_alert(self, sensor: "Sensor", readings: "list[SensorReading]") -> Alert | None:
        """Raise ``sensor_flatline`` on a fresh sensor flat at 0 % / 100 %; resolve it once the series moves."""
        found = _flatline(readings)
        if found is None:
            clear_alert(
                self._repo,
                source=SOURCE_ANOMALY,
                code="sensor_flatline",
                cluster_id=sensor.cluster_id,
                sensor_id=sensor.id,
            )
            return None
        level, span = found
        logger.warning("Sensor flatline %d (%s): soil %.1f%% for %ds", sensor.id, sensor.name, level, span)
        return raise_alert(
            self._repo,
            notifier=self._notifier,
            source=SOURCE_ANOMALY,
            code="sensor_flatline",
            severity="warning",
            title=f"Sensor flat-lined: {sensor.name}",
            message=(
                f"{sensor.name}: soil moisture flat at {level:.1f}% for {span // SECONDS_PER_HOUR} h while "
                "readings keep arriving (probe out of the soil, in water, or faulty?)"
            ),
            cluster_id=sensor.cluster_id,
            sensor_id=sensor.id,
            payload={"sensor_id": sensor.id, "sensor_name": sensor.name, "level": level, "span_seconds": span},
        )

    def _drift_unless_step(
        self, sensor: "Sensor", window: "list[SensorReading]", median_interval: float | None
    ) -> Alert | None:
        """Judge drift, except on a rising step that holds: that is a watering, so resolve ``sensor_drift``."""
        if _is_rising_step(window):
            clear_alert(
                self._repo,
                source=SOURCE_ANOMALY,
                code="sensor_drift",
                cluster_id=sensor.cluster_id,
                sensor_id=sensor.id,
            )
            return None
        return self._drift_alert(sensor, window, median_interval)

    def _drift_alert(
        self, sensor: "Sensor", window: "list[SensorReading]", median_interval: float | None
    ) -> Alert | None:
        """Raise ``sensor_drift`` when the latest soil reading is a > 4-sigma outlier against the window."""
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
                    f"is a {z:+.1f}\N{GREEK SMALL LETTER SIGMA} outlier (mean={mean:.1f}%, std={std:.1f}%)"
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
