"""Sensor data aggregation for irrigation decisions."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from greenhouse_core.constants import NIGHT_LUX_THRESHOLD
from greenhouse_core.logic.cleaning import clean_readings
from greenhouse_core.logic.decision import PerSensorSnapshot, SensorSnapshot
from greenhouse_core.repository import IrrigationRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.logic.cleaning import CleanedReading
    from greenhouse_core.models import Sensor


def _mean_or_none(values: Sequence[float]) -> float | None:
    """Mean of the values, or None when there are none — "no data" is not zero."""
    return statistics.mean(values) if values else None


def _per_sensor_snapshot(sensor: Sensor, readings: Sequence[CleanedReading]) -> PerSensorSnapshot:
    """One sensor's own averages, so the audit log can show which plant drove the call."""
    return PerSensorSnapshot(
        sensor_id=sensor.id,
        plant_id=sensor.plant_id,
        name=sensor.name,
        avg_temperature=_mean_or_none([r.temperature for r in readings if r.temperature is not None]),
        avg_humidity=_mean_or_none([r.env_humidity for r in readings if r.env_humidity is not None]),
        avg_soil_moisture=_mean_or_none([r.soil_moisture for r in readings if r.soil_moisture is not None]),
    )


def get_recent_sensor_data(db: IrrigationRepository, cluster_id: int, hours: int = 24) -> SensorSnapshot:
    """Build a typed snapshot of cluster sensors over the lookback window.

    The snapshot drives every downstream rule (stress, conflict, adjustments)
    and is persisted with the decision so the audit log can replay the inputs.
    """
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return SensorSnapshot()

    # Clean per sensor (range-gate + spike-reject) so spurious samples don't
    # drag the cluster aggregates — especially min_soil_moisture, which the
    # engine uses as "the driest plant drives the call".
    cleaned = [(sensor, clean_readings(db.get_recent_readings(sensor.id, hours=hours))) for sensor in sensors]
    pooled = [r for _, readings in cleaned for r in readings]
    all_soil = [r.soil_moisture for r in pooled if r.soil_moisture is not None]
    daytime_light = [r.light for r in pooled if r.light is not None and r.light > NIGHT_LUX_THRESHOLD]
    water_warnings = {sensor.name for sensor, readings in cleaned if any(r.water_warning for r in readings)}

    return SensorSnapshot(
        avg_temperature=_mean_or_none([r.temperature for r in pooled if r.temperature is not None]),
        avg_env_humidity=_mean_or_none([r.env_humidity for r in pooled if r.env_humidity is not None]),
        avg_soil_moisture=_mean_or_none(all_soil),
        min_soil_moisture=min(all_soil) if all_soil else None,
        max_soil_moisture=max(all_soil) if all_soil else None,
        avg_light=_mean_or_none(daytime_light),
        per_sensor=[_per_sensor_snapshot(sensor, readings) for sensor, readings in cleaned],
        water_warnings=sorted(water_warnings),
    )
