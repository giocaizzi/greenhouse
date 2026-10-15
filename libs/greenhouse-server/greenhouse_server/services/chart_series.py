"""Chart series labelled by plant: each sensor's points split along its assignment history.

A sensor that moves between plants measures a different plant after the move, so a chart
labelled only by sensor name hides what the series means. Every series here carries the
plant the sensor was measuring (``SensorAssignment``), the sensor name as secondary
information (``"<species> · <sensor>"``), and ``"<sensor> (unassigned)"`` for a period with
no assignment. Display only: nothing that judges readings goes through this module.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

from greenhouse_core.constants import SECONDS_PER_HOUR

if TYPE_CHECKING:
    from greenhouse_core.models import Plant, Sensor, SensorAssignment
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.chart_payload import ChartDataset
    from greenhouse_server.services.charts import Metric

Point = tuple[int, float]


def series_label(sensor_name: str, plant_name: str | None) -> str:
    """``"<plant> · <sensor>"``, or ``"<sensor> (unassigned)"`` without a plant."""
    return f"{plant_name} · {sensor_name}" if plant_name is not None else f"{sensor_name} (unassigned)"


def plant_sensor_datasets(repo: IrrigationRepository, plant: Plant, hours: int, metric: Metric) -> list[ChartDataset]:
    """One plant's series: one per sensor that served it in the window, readings inside its assignments."""
    since = int(time.time()) - hours * SECONDS_PER_HOUR
    by_sensor: dict[int, list[Point]] = defaultdict(list)
    for r in repo.readings_for_plant(plant.id, since_ts=since):
        val = getattr(r, metric, None)
        if val is not None:
            by_sensor[r.sensor_id].append((r.timestamp, float(val)))
    names = {s.id: s.name for s in repo.list_sensors_by_ids(by_sensor.keys())} if by_sensor else {}
    datasets: list[ChartDataset] = []
    for sensor_id, points in by_sensor.items():
        sensor_name = names.get(sensor_id, f"sensor#{sensor_id}")
        points.sort(key=lambda p: p[0])
        datasets.append(_dataset(sensor_id, sensor_name, plant.id, plant.species, points))
    return datasets


def cluster_sensor_datasets(
    repo: IrrigationRepository, sensors: list[Sensor], hours: int, metric: Metric
) -> list[ChartDataset]:
    """Every sensor's series, split into one dataset per run of readings under the same plant."""
    plant_names: dict[int, str] = {}
    datasets: list[ChartDataset] = []
    for sensor in sensors:
        points = sorted(
            (r.timestamp, float(v))
            for r in repo.get_recent_readings(sensor.id, hours=hours)
            if (v := getattr(r, metric, None)) is not None
        )
        assignments = repo.list_sensor_assignments(sensor.id)
        for plant_id, segment in _segments(points, assignments):
            name = _plant_name(repo, plant_id, plant_names) if plant_id is not None else None
            datasets.append(_dataset(sensor.id, sensor.name, plant_id, name, segment))
    return datasets


def _segments(points: list[Point], assignments: list[SensorAssignment]) -> list[tuple[int | None, list[Point]]]:
    """Split ASC ``points`` into consecutive runs measured under the same plant (``None``: unassigned)."""
    runs: list[tuple[int | None, list[Point]]] = []
    for point in points:
        plant_id = _plant_at(point[0], assignments)
        if runs and runs[-1][0] == plant_id:
            runs[-1][1].append(point)
        else:
            runs.append((plant_id, [point]))
    return runs


def _plant_at(ts: int, assignments: list[SensorAssignment]) -> int | None:
    """The plant whose assignment covers ``ts`` (start inclusive, end exclusive), or ``None``.

    The first assignment reaches back to 0, like the judging windows: the Tuya backfill
    stores readings from before the sensor was registered, and they belong to its first plant.
    """
    for i, a in enumerate(assignments):
        if (i == 0 or a.started_at <= ts) and (a.ended_at is None or ts < a.ended_at):
            return a.plant_id
    return None


def _plant_name(repo: IrrigationRepository, plant_id: int, cache: dict[int, str]) -> str:
    """The plant's species (what the UI calls a plant), ``plant #<id>`` once it was deleted."""
    if plant_id not in cache:
        plant = repo.get_plant(plant_id)
        cache[plant_id] = plant.species if plant is not None else f"plant #{plant_id}"
    return cache[plant_id]


def _dataset(
    sensor_id: int, sensor_name: str, plant_id: int | None, plant_name: str | None, points: list[Point]
) -> ChartDataset:
    return {
        "sensor_id": sensor_id,
        "sensor_name": sensor_name,
        "plant_id": plant_id,
        "label": series_label(sensor_name, plant_name),
        "points": points,
    }
