"""Irrigation and monitoring orchestration."""

import json
import urllib.request

from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.logic import IrrigationLogic
from tuya_irrigation_core.plant_db import get_plant_database
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts
from tuya_irrigation_server.services.sync import sync_and_read_sensors


def fetch_weather(lat: float = 45.464, lon: float = 9.189) -> dict | None:
    """Fetch current weather from Open-Meteo."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
        f"&timezone=Europe/Rome"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            current = data.get("current", {})
            return {
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "precipitation": current.get("precipitation"),
                "humidity": current.get("relative_humidity_2m"),
            }
    except Exception:
        return None


def _resolve_temperature(
    repo: IrrigationRepository,
    cluster_id: int,
    is_indoor: bool,
    temp_override: float | None,
    no_sync: bool,
) -> tuple[float, str, dict | None]:
    """Resolve temperature from override, sensor, or weather. Returns (temp, source, sensor_data)."""
    if temp_override is not None:
        return temp_override, "override", None

    sensor_data = None if no_sync else sync_and_read_sensors(repo, cluster_id)
    weather = None

    if is_indoor:
        if sensor_data and sensor_data.get("temperature") is not None:
            return sensor_data["temperature"], "sensor", sensor_data
        weather = fetch_weather()
        if weather and weather.get("feels_like") is not None:
            return weather["feels_like"], "open-meteo (fallback)", sensor_data
    else:
        weather = fetch_weather()
        if weather and weather.get("feels_like") is not None:
            return weather["feels_like"], "open-meteo", sensor_data
        if sensor_data and sensor_data.get("temperature") is not None:
            return sensor_data["temperature"], "sensor (weather unavailable)", sensor_data

    return 20.0, "fallback (20C)", sensor_data


def run_irrigation_pipeline(
    repo: IrrigationRepository,
    cluster_id: int,
    dm: TuyaDeviceManager | None,
    temp_override: float | None = None,
    dry_run: bool = False,
    no_sync: bool = False,
) -> dict:
    """Full pipeline: sync → weather → decide → execute. Returns result dict."""
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        return {"action": "error", "reason": "cluster not found", "confidence": 0}

    is_indoor = cluster.environment == "indoor"
    temp, source, sensor_data = _resolve_temperature(repo, cluster_id, is_indoor, temp_override, no_sync)

    logic = IrrigationLogic(repo)
    decision = logic.decide_for_cluster(cluster_id, current_temp=temp)
    if not decision:
        return {"action": "error", "reason": "no data for decision", "confidence": 0}

    result = {
        "action": decision["action"],
        "reason": decision["reason"],
        "confidence": decision["confidence"],
        "duration_minutes": decision["duration_minutes"],
        "interval_hours": decision["interval_hours"],
        "stress_indicators": decision.get("stress_indicators"),
        "temperature": temp,
        "temperature_source": source,
    }

    if dry_run or decision["action"] == "skip":
        return result

    # Execute
    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    if not irrigators:
        result["action"] = "error"
        result["reason"] = "no irrigators found"
        return result
    if dm is None:
        result["action"] = "error"
        result["reason"] = "no device manager"
        return result

    irrigator = irrigators[0]
    duration = decision["duration_minutes"]
    success, output = dm.irrigator_start(irrigator, duration)

    soil_note = (
        f", soil={sensor_data['soil_moisture']:.0f}%"
        if sensor_data and sensor_data.get("soil_moisture") is not None
        else ""
    )
    repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action="start" if success else "attempted",
        duration_minutes=duration,
        triggered_by="auto",
        notes=f"temp={temp:.1f}C ({source}){soil_note}, confidence={decision['confidence']:.0%}, reason={decision['reason']}",
    )
    repo.session.commit()

    if not success:
        result["action"] = "error"
        result["reason"] = f"irrigator failed: {output}"

    result["action"] = "irrigated" if success else "error"
    return result


def monitor_cluster(repo: IrrigationRepository, cluster_id: int, no_sync: bool = False) -> dict:
    """Monitor sensor-only cluster. Returns per-sensor soil status."""
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        return {"cluster_name": "unknown", "sensors": [], "needs_water": []}

    if not no_sync:
        sync_and_read_sensors(repo, cluster_id)

    sensors = repo.get_sensors_in_cluster(cluster_id)
    plant_db = get_plant_database()
    plants_by_id = {p.id: p for p in repo.get_plants_in_cluster(cluster_id)}

    sensor_statuses = []
    needs_water = []

    for sensor in sensors:
        readings = repo.get_recent_readings(sensor.id, hours=2)
        latest_soil = (
            next((r.soil_moisture for r in readings if r.soil_moisture is not None), None) if readings else None
        )

        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        care = plant_db.get_care_data(species=plant.species if plant else None)
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


def check_cluster(
    repo: IrrigationRepository,
    cluster_id: int,
    dm: TuyaDeviceManager | None,
) -> dict:
    """Check a single cluster: irrigate if has irrigators, monitor otherwise."""
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        return {"cluster_id": cluster_id, "cluster_name": "unknown", "action": "error", "notes": "not found"}

    irrigators = repo.get_irrigators_in_cluster(cluster_id)
    alerts = collect_learning_alerts(repo, cluster_id)
    maintenance = collect_maintenance_alerts(repo, cluster_id)

    if irrigators:
        # Config check
        config = repo.get_irrigation_config(cluster_id)
        if config and not config.auto_run:
            return {
                "cluster_id": cluster_id,
                "cluster_name": cluster.name,
                "action": "skipped",
                "notes": "auto_run disabled",
                "alerts": alerts,
                "maintenance": maintenance,
            }

        result = run_irrigation_pipeline(repo, cluster_id, dm)
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster.name,
            "action": result.get("action", "error"),
            "notes": result.get("reason", ""),
            "alerts": alerts,
            "maintenance": maintenance,
        }
    else:
        monitor = monitor_cluster(repo, cluster_id)
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster.name,
            "action": "monitored",
            "needs_water": monitor.get("needs_water", []),
            "alerts": alerts,
            "maintenance": maintenance,
        }


def check_all_clusters(repo: IrrigationRepository, dm: TuyaDeviceManager | None) -> list[dict]:
    """Check all clusters."""
    clusters = repo.list_clusters()
    return [check_cluster(repo, c.id, dm) for c in clusters]
