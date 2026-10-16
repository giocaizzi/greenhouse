"""Soil range strip read model: the cluster's band, each plant's current soil value, and their average.

Display only (issue #201). The engine decides on the driest plant (invariant 2),
so the average here is labelled ``avg`` wherever it is drawn and never feeds a
decision. Whether a plant is dry is a judgement, so each plant's value is judged
the engine's way (invariant 10): the driest current *cleaned* reading among its
sensors, from the decision snapshot via :func:`current_soil`, against the plant's
own band (:func:`plant_care_data`). The shaded range is the cluster's widest band,
what an unlinked sensor is judged against.
"""

import statistics
import time
from typing import TYPE_CHECKING, Literal, TypedDict

from greenhouse_core.constants import SNAPSHOT_LOOKBACK_HOURS
from greenhouse_core.logic.decision import SensorSnapshot
from greenhouse_core.logic.plant_needs import cluster_band, current_soil, plant_bands
from greenhouse_core.logic.sensors import get_recent_sensor_data
from greenhouse_core.plant_db import PlantDatabase, plant_care_data

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.models import Plant
    from greenhouse_core.repository import IrrigationRepository

SoilPosition = Literal["dry", "in band", "wet"]


class SoilStripMark(TypedDict):
    """One plant's current soil value and where it sits against the plant's own band."""

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


def _plant_soil(snapshot: SensorSnapshot, plant_id: int, bands: dict[int, tuple[float, float]]) -> float | None:
    """The driest current cleaned soil value among the plant's sensors, or None when none is current."""
    own = SensorSnapshot(per_sensor=[s for s in snapshot.per_sensor if s.plant_id == plant_id])
    return min((s.value for s in current_soil(own, bands, bands[plant_id])), default=None)


def soil_strip(
    repo: "IrrigationRepository", plant_db: PlantDatabase, cluster_id: int, plants: "Sequence[Plant]"
) -> SoilStrip | None:
    """Build the strip for a cluster's plants, or None when it has no plants."""
    if not plants:
        return None
    care = [plant_care_data(plant_db, p) for p in plants]
    bands = plant_bands(plants, care)
    snapshot = get_recent_sensor_data(repo, cluster_id, hours=SNAPSHOT_LOOKBACK_HOURS, now=int(time.time()))
    marks: list[SoilStripMark] = []
    for plant in plants:
        value = _plant_soil(snapshot, plant.id, bands)
        if value is not None:
            band = bands[plant.id]
            marks.append({"plant": plant.species, "value": value, "band": band, "position": _position(value, band)})
    average = statistics.fmean(m["value"] for m in marks) if marks else None
    return {"band": cluster_band(care), "marks": marks, "average": average}
