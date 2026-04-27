"""Per-plant health score computation and daily snapshot service."""

import time
from datetime import UTC, datetime

from tuya_irrigation_core.learning.profiling import get_plant_profile
from tuya_irrigation_core.logic.plant_needs import parse_moisture_target
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository


class PlantHealthService:
    """Computes and persists a 0–100 composite health score for each plant."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def compute_score(self, plant_id: int, *, days: int = 14) -> dict:
        """Compute the composite health score for a single plant over the last ``days``.

        Score formula (0–100): mean of whichever subset of the four components
        is available — ``soil_in_band_pct``, ``temp_in_band_pct``,
        ``humidity_in_band_pct``, and ``efficiency * 100`` — clipped to [0, 100].
        Each component is included only when the underlying data exists (e.g.
        efficiency is skipped when there are fewer than 3 irrigation events).

        Args:
            plant_id: Database ID of the plant to evaluate.
            days: Look-back window in days for sensor readings.

        Returns:
            dict with keys: score (float | None), soil_in_band_pct (float | None),
            temp_in_band_pct (float | None), humidity_in_band_pct (float | None),
            efficiency (float | None), sample_count (int).
            score is None when no readings and no efficiency are available.
        """
        plant = self._repo.get_plant(plant_id)
        if not plant:
            return {
                "score": None,
                "soil_in_band_pct": None,
                "temp_in_band_pct": None,
                "humidity_in_band_pct": None,
                "efficiency": None,
                "sample_count": 0,
            }

        care = self._plant_db.get_care_data(species=plant.species, category=plant.category)

        soil_min, soil_max = parse_moisture_target(care.get("soil_moisture_target", "45-65"))
        temp_min = care.get("ideal_temp_min_c")
        temp_max = care.get("ideal_temp_max_c")
        hum_min = care.get("ideal_humidity_min")
        hum_max = care.get("ideal_humidity_max")

        sensors = list(plant.sensors)
        all_readings = []
        for sensor in sensors:
            all_readings.extend(self._repo.get_recent_readings(sensor.id, hours=days * 24))

        sample_count = len(all_readings)

        soil_in_band_pct: float | None = None
        temp_in_band_pct: float | None = None
        humidity_in_band_pct: float | None = None

        if all_readings:
            soil_readings = [r.soil_moisture for r in all_readings if r.soil_moisture is not None]
            if soil_readings:
                in_band = sum(1 for v in soil_readings if soil_min <= v <= soil_max)
                soil_in_band_pct = in_band / len(soil_readings) * 100

            if temp_min is not None and temp_max is not None:
                temp_readings = [r.temperature for r in all_readings if r.temperature is not None]
                if temp_readings:
                    in_band = sum(1 for v in temp_readings if temp_min <= v <= temp_max)
                    temp_in_band_pct = in_band / len(temp_readings) * 100

            if hum_min is not None and hum_max is not None:
                hum_readings = [r.env_humidity for r in all_readings if r.env_humidity is not None]
                if hum_readings:
                    in_band = sum(1 for v in hum_readings if hum_min <= v <= hum_max)
                    humidity_in_band_pct = in_band / len(hum_readings) * 100

        efficiency: float | None = None
        for sensor in sensors:
            profile = get_plant_profile(self._repo, sensor, days=days)
            if profile is not None:
                efficiency = profile.efficiency_score
                break

        components = [
            c
            for c in [
                soil_in_band_pct,
                temp_in_band_pct,
                humidity_in_band_pct,
                efficiency * 100 if efficiency is not None else None,
            ]
            if c is not None
        ]

        score: float | None = None
        if components:
            score = float(max(0.0, min(100.0, round(sum(components) / len(components)))))

        return {
            "score": score,
            "soil_in_band_pct": soil_in_band_pct,
            "temp_in_band_pct": temp_in_band_pct,
            "humidity_in_band_pct": humidity_in_band_pct,
            "efficiency": efficiency,
            "sample_count": sample_count,
        }

    def snapshot_daily(self) -> int:
        """Compute today's health score for every plant and upsert to plant_health_daily.

        Returns:
            Number of rows written (one per plant regardless of whether it was
            inserted or updated).
        """
        date_key = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        now = int(time.time())
        plants = self._repo.list_all_plants()
        rows_written = 0
        for plant in plants:
            result = self.compute_score(plant.id)
            score = result["score"]
            if score is None:
                continue
            self._repo.upsert_plant_health(
                plant_id=plant.id,
                date_key=date_key,
                score=score,
                soil_in_band_pct=result["soil_in_band_pct"],
                temp_in_band_pct=result["temp_in_band_pct"],
                humidity_in_band_pct=result["humidity_in_band_pct"],
                efficiency=result["efficiency"],
                sample_count=result["sample_count"],
                timestamp=now,
            )
            rows_written += 1
        return rows_written
