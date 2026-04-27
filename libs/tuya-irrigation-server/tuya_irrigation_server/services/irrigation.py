"""Irrigation and monitoring orchestration."""

from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.logic import IrrigationLogic
from tuya_irrigation_core.models import ENTITY_CLUSTER
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_server.services.alerts import raise_alert, sync_cluster_alerts
from tuya_irrigation_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts
from tuya_irrigation_server.services.sync import SyncService
from tuya_irrigation_server.services.weather import WeatherClient


class IrrigationService:
    """Orchestrates irrigation decisions, execution, and monitoring."""

    def __init__(
        self,
        repo: IrrigationRepository,
        dm: TuyaDeviceManager | None,
        sync_service: SyncService,
        weather_client: WeatherClient,
        plant_db: PlantDatabase,
    ):
        self._repo = repo
        self._dm = dm
        self._sync = sync_service
        self._weather = weather_client
        self._plant_db = plant_db

    def _resolve_temperature(
        self,
        cluster_id: int,
        is_indoor: bool,
        temp_override: float | None,
        no_sync: bool,
    ) -> tuple[float, str, dict | None]:
        """Resolve temperature from override, sensor, or weather. Returns (temp, source, sensor_data)."""
        if temp_override is not None:
            return temp_override, "override", None

        sensor_data = None if no_sync else self._sync.sync_and_read_sensors(cluster_id)
        weather = None

        if is_indoor:
            if sensor_data and sensor_data.get("temperature") is not None:
                return sensor_data["temperature"], "sensor", sensor_data
            weather = self._weather.get_current()
            if weather and weather.get("feels_like") is not None:
                return weather["feels_like"], "open-meteo (fallback)", sensor_data
        else:
            weather = self._weather.get_current()
            if weather and weather.get("feels_like") is not None:
                return weather["feels_like"], "open-meteo", sensor_data
            if sensor_data and sensor_data.get("temperature") is not None:
                return sensor_data["temperature"], "sensor (weather unavailable)", sensor_data

        return 20.0, "fallback (20C)", sensor_data

    def run_irrigation_pipeline(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
    ) -> dict:
        """Full pipeline: sync -> weather -> decide -> execute. Returns result dict."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"action": "error", "reason": "cluster not found", "confidence": 0}

        is_indoor = cluster.environment == "indoor"
        temp, source, sensor_data = self._resolve_temperature(cluster_id, is_indoor, temp_override, no_sync)

        logic = IrrigationLogic(self._repo, self._plant_db)
        decision = logic.decide_for_cluster(cluster_id, current_temp=temp, persist=True)
        if not decision:
            return {"action": "error", "reason": "no data for decision", "confidence": 0}

        result = {
            "action": decision.action.value,
            "reason": decision.reason_text,
            "confidence": decision.confidence,
            "duration_minutes": decision.duration_minutes,
            "interval_hours": decision.interval_hours,
            "stress_indicators": decision.stress_indicators.model_dump(exclude_none=True),
            "reasons": [r.model_dump() for r in decision.reasons],
            "temperature": temp,
            "temperature_source": source,
        }

        if dry_run or decision.action.value == "skip":
            if not dry_run:
                self._repo.add_activity_event(
                    source="irrigation",
                    entity_type=ENTITY_CLUSTER,
                    entity_id=cluster_id,
                    code="decision_skip",
                    message=decision.reason_text,
                    severity="info",
                )
            return result

        # Execute
        irrigators = self._repo.get_irrigators_in_cluster(cluster_id)
        if not irrigators:
            result["action"] = "error"
            result["reason"] = "no irrigators found"
            return result
        if self._dm is None:
            result["action"] = "error"
            result["reason"] = "no device manager"
            return result

        irrigator = irrigators[0]
        duration = decision.duration_minutes
        success, output = self._dm.irrigator_start(irrigator, duration)

        soil_note = (
            f", soil={sensor_data['soil_moisture']:.0f}%"
            if sensor_data and sensor_data.get("soil_moisture") is not None
            else ""
        )
        self._repo.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start" if success else "attempted",
            duration_minutes=duration,
            triggered_by="auto",
            notes=(
                f"temp={temp:.1f}C ({source}){soil_note}, "
                f"confidence={decision.confidence:.0%}, reason={decision.reason_text}"
            ),
        )

        if success:
            self._repo.add_activity_event(
                source="irrigation",
                entity_type=ENTITY_CLUSTER,
                entity_id=cluster_id,
                code="irrigated",
                message=f"irrigated for {duration}min (confidence={decision.confidence:.0%})",
                severity="info",
                payload={
                    "irrigator_id": irrigator.id,
                    "duration_minutes": duration,
                    "confidence": decision.confidence,
                },
            )
            result["action"] = "irrigated"
        else:
            self._repo.add_activity_event(
                source="irrigation",
                entity_type=ENTITY_CLUSTER,
                entity_id=cluster_id,
                code="actuation_failed",
                message=f"irrigator failed: {output}",
                severity="warning",
            )
            raise_alert(
                self._repo,
                source="irrigation",
                code="actuation_failed",
                title="Irrigation Actuation Failed",
                message=f"Irrigator '{irrigator.name}' failed to start: {output}",
                severity="warning",
                cluster_id=cluster_id,
            )
            result["action"] = "error"
            result["reason"] = f"irrigator failed: {output}"

        return result

    def monitor_cluster(self, cluster_id: int, no_sync: bool = False) -> dict:
        """Monitor sensor-only cluster. Returns per-sensor soil status."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_name": "unknown", "sensors": [], "needs_water": []}

        if not no_sync:
            self._sync.sync_and_read_sensors(cluster_id)

        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        plants_by_id = {p.id: p for p in self._repo.get_plants_in_cluster(cluster_id)}

        sensor_statuses = []
        needs_water = []

        for sensor in sensors:
            readings = self._repo.get_recent_readings(sensor.id, hours=2)
            latest_soil = (
                next((r.soil_moisture for r in readings if r.soil_moisture is not None), None) if readings else None
            )

            plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
            care = self._plant_db.get_care_data(species=plant.species if plant else None)
            target_raw = care.get("soil_moisture_target", "45-65")
            try:
                t_min, t_max = (float(x) for x in target_raw.split("-"))
            except Exception:
                t_min, t_max = 45.0, 65.0

            if latest_soil is None:
                status = "no_data"
            elif latest_soil < t_min - 15:
                status = "very_dry"
            elif latest_soil < t_min:
                status = "dry"
            elif latest_soil > t_max + 10:
                status = "wet"
            else:
                status = "ok"

            sensor_statuses.append(
                {
                    "sensor_id": sensor.id,
                    "sensor_name": sensor.name,
                    "plant_species": plant.species if plant else None,
                    "soil_moisture": latest_soil,
                    "status": status,
                    "target_min": t_min,
                    "target_max": t_max,
                }
            )

            if status in ("very_dry", "dry"):
                needs_water.append(f"{sensor.name} ({plant.species if plant else 'unknown'}): {latest_soil:.0f}%")

        return {
            "cluster_name": cluster.name,
            "sensors": sensor_statuses,
            "needs_water": needs_water,
        }

    def check_cluster(self, cluster_id: int) -> dict:
        """Check a single cluster: irrigate if has irrigators, monitor otherwise."""
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return {"cluster_id": cluster_id, "cluster_name": "unknown", "action": "error", "notes": "not found"}

        irrigators = self._repo.get_irrigators_in_cluster(cluster_id)
        alerts = collect_learning_alerts(self._repo, cluster_id, self._plant_db)
        maintenance = collect_maintenance_alerts(self._repo, cluster_id, self._plant_db)

        if irrigators:
            config = self._repo.get_irrigation_config(cluster_id)
            if config and not config.auto_run:
                sync_cluster_alerts(self._repo, cluster_id, self._plant_db)
                return {
                    "cluster_id": cluster_id,
                    "cluster_name": cluster.name,
                    "action": "skipped",
                    "notes": "auto_run disabled",
                    "alerts": alerts,
                    "maintenance": maintenance,
                }

            result = self.run_irrigation_pipeline(cluster_id)
            sync_cluster_alerts(self._repo, cluster_id, self._plant_db)
            return {
                "cluster_id": cluster_id,
                "cluster_name": cluster.name,
                "action": result.get("action", "error"),
                "notes": result.get("reason", ""),
                "alerts": alerts,
                "maintenance": maintenance,
            }
        else:
            monitor = self.monitor_cluster(cluster_id)
            sync_cluster_alerts(self._repo, cluster_id, self._plant_db)
            return {
                "cluster_id": cluster_id,
                "cluster_name": cluster.name,
                "action": "monitored",
                "needs_water": monitor.get("needs_water", []),
                "alerts": alerts,
                "maintenance": maintenance,
            }

    def check_all_clusters(self) -> list[dict]:
        """Check all clusters."""
        clusters = self._repo.list_clusters()
        return [self.check_cluster(c.id) for c in clusters]
