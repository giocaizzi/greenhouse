"""Next-irrigation forecast service."""

import time
from dataclasses import dataclass

from greenhouse_core.learning import IrrigationLearner
from greenhouse_core.logic.plant_needs import parse_moisture_target
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import ForecastResponse

_FALLBACK_DRAINAGE_PER_HOUR = -2.0  # %/h, used when no learned profile is available
_WEATHER_PRECIP_THRESHOLD_MM = 2.0


@dataclass
class _SensorForecast:
    hours_until_next: float
    current_moisture: float
    target_min: float
    label: str
    has_profile: bool
    drainage_rate: float


class ForecastService:
    """Predicts when a cluster will next need irrigation."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase, weather_client=None):
        self._repo = repo
        self._plant_db = plant_db
        self._weather = weather_client

    def predict_next_irrigation(self, cluster_id: int) -> ForecastResponse:
        """Compute a next-irrigation forecast for a cluster.

        Uses per-plant drainage profiles (learned from history) to project how
        long before the driest plant crosses its target-minimum moisture.
        Falls back to a constant -2.0 %/h rate when no profile exists.
        """
        cluster = self._repo.get_cluster(cluster_id)
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants = self._repo.get_plants_in_cluster(cluster_id)
        plant_map = {p.id: p for p in plants}
        learner = IrrigationLearner(self._repo, self._plant_db)

        now = int(time.time())

        sensor_forecasts: list[_SensorForecast] = []

        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=24)
            current_moisture = next((r.soil_moisture for r in readings if r.soil_moisture is not None), None)
            if current_moisture is None:
                continue

            plant = plant_map.get(sensor.plant_id) if sensor.plant_id else None
            care = self._plant_db.get_care_data(
                species=plant.species if plant else None,
                category=plant.category if plant else None,
            )
            target_min, _ = parse_moisture_target(care.get("soil_moisture_target", "45-65"))

            profile = learner.get_plant_profile(sensor)
            has_profile = profile is not None
            drainage = profile.avg_drainage_per_hour if has_profile else _FALLBACK_DRAINAGE_PER_HOUR
            if drainage >= 0:
                drainage = _FALLBACK_DRAINAGE_PER_HOUR

            if current_moisture <= target_min:
                hours = 0.0
            else:
                hours = (current_moisture - target_min) / abs(drainage)

            label = plant.species if plant else sensor.name
            sensor_forecasts.append(
                _SensorForecast(
                    hours_until_next=hours,
                    current_moisture=current_moisture,
                    target_min=target_min,
                    label=label,
                    has_profile=has_profile,
                    drainage_rate=drainage,
                )
            )

        if not sensor_forecasts:
            return ForecastResponse(
                cluster_id=cluster_id,
                next_predicted_at=None,
                hours_until_next=None,
                projected_min_moisture=None,
                method="fallback_constant",
                confidence=0.2,
                explanation="No sensors with soil moisture data available.",
            )

        # Driest plant (shortest time to threshold) drives the forecast
        sensor_forecasts.sort(key=lambda f: f.hours_until_next)
        driver = sensor_forecasts[0]

        profiled_count = sum(1 for f in sensor_forecasts if f.has_profile)
        if profiled_count >= 3:
            confidence = 0.7
        elif profiled_count >= 1:
            confidence = 0.4
        else:
            confidence = 0.2

        method = "drainage_slope" if driver.has_profile else "fallback_constant"
        explanation = (
            f"{driver.label} will hit its {driver.target_min:.0f}% min in ~{driver.hours_until_next:.1f}h "
            f"based on {driver.drainage_rate:.1f}%/h drainage."
        )

        next_predicted_at = int(now + driver.hours_until_next * 3600)

        weather_skip = False
        weather_reason: str | None = None
        precipitation_next_6h_mm: float | None = None

        is_outdoor = cluster is not None and cluster.environment != "indoor"
        if is_outdoor and self._weather is not None:
            forecast = self._weather.get_forecast(hours=6)
            if forecast is not None:
                precip = forecast.get("precipitation_mm", 0.0) or 0.0
                precipitation_next_6h_mm = precip
                if precip > _WEATHER_PRECIP_THRESHOLD_MM:
                    weather_skip = True
                    weather_reason = f"rain forecast ({precip:.1f}mm in next 6h)"

        return ForecastResponse(
            cluster_id=cluster_id,
            next_predicted_at=next_predicted_at,
            hours_until_next=round(driver.hours_until_next, 2),
            projected_min_moisture=round(driver.current_moisture, 1),
            method=method,
            confidence=confidence,
            explanation=explanation,
            weather_skip=weather_skip,
            weather_reason=weather_reason,
            precipitation_next_6h_mm=precipitation_next_6h_mm,
        )
