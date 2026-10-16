"""Soil range strip read model: the cluster's band, each plant's current reading, and their average.

Display only (issue #201). The engine decides on the driest plant (invariant 2),
so the average here is labelled ``avg`` wherever it is drawn and never feeds a
decision. Bands come from the same resolution the engine uses: each plant's own
target via :func:`plant_care_data`, and the cluster's widest band as the shaded
range (what an unlinked sensor is judged against).
"""

import statistics
from typing import TYPE_CHECKING, Literal, TypedDict

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_core.logic.plant_needs import cluster_band, plant_bands
from greenhouse_core.plant_db import PlantDatabase, plant_care_data

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.models import Plant
    from greenhouse_server.services.cluster import SensorStatusRow

SoilPosition = Literal["dry", "in band", "wet"]


class SoilStripMark(TypedDict):
    """One plant's current soil reading and where it sits against the plant's own band."""

    plant: str
    value: float
    band: tuple[float, float]
    position: SoilPosition


class SoilStrip(TypedDict):
    """``soil_strip`` result: the shaded band, one mark per plant with a current reading, and their mean."""

    band: tuple[float, float]
    marks: list[SoilStripMark]
    average: float | None


def _position(value: float, band: tuple[float, float]) -> SoilPosition:
    if value < band[0]:
        return "dry"
    if value > band[1]:
        return "wet"
    return "in band"


def _current_soil(plant_id: int, sensors: "Sequence[SensorStatusRow]") -> float | None:
    """The plant's first linked sensor's newest soil reading, or None when absent or stale.

    The first linked sensor is the one the card's plant row shows, so the mark
    and the number beside the plant agree.
    """
    sensor = next((s for s in sensors if s["plant_id"] == plant_id), None)
    if sensor is None or sensor["last_reading"] is None:
        return None
    age = sensor["reading_age_seconds"]
    if age is not None and age > SENSOR_READING_STALE_SECONDS:
        return None
    return sensor["last_reading"].soil_moisture


def soil_strip(
    plant_db: PlantDatabase, plants: "Sequence[Plant]", sensors: "Sequence[SensorStatusRow]"
) -> SoilStrip | None:
    """Build the strip for a cluster's plants and sensor status rows, or None when it has no plants."""
    if not plants:
        return None
    care = [plant_care_data(plant_db, p) for p in plants]
    bands = plant_bands(plants, care)
    marks: list[SoilStripMark] = []
    for plant in plants:
        value = _current_soil(plant.id, sensors)
        if value is not None:
            band = bands[plant.id]
            marks.append({"plant": plant.species, "value": value, "band": band, "position": _position(value, band)})
    average = statistics.fmean(m["value"] for m in marks) if marks else None
    return {"band": cluster_band(care), "marks": marks, "average": average}
