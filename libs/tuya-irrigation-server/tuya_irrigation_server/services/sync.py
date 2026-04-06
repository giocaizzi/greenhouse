"""Sensor sync orchestration service."""

from tuya_irrigation_core.cloud import TuyaCloud
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.sync import _sync_single_sensor
from tuya_irrigation_core.sync import sync_sensor_data as core_sync


def sync_all_sensors(repo: IrrigationRepository, hours: int = 24) -> dict:
    """Sync all sensor data from Tuya Cloud. Returns stats dict."""
    cloud = TuyaCloud()
    stats = core_sync(repo, cloud, hours=hours)
    repo.session.commit()
    return stats


def sync_and_read_sensors(repo: IrrigationRepository, cluster_id: int) -> dict | None:
    """Sync cluster sensors and return live reading from first sensor."""
    sensors = repo.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return None
    try:
        cloud = TuyaCloud()
        for sensor in sensors:
            try:
                _sync_single_sensor(repo, cloud, sensor, hours=6)
            except Exception:
                pass
        repo.session.commit()
        live = cloud.get_live_reading(sensors[0].tuya_device_id)
        return live if live else None
    except Exception:
        return None
