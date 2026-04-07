"""Sensor sync orchestration service."""

from tuya_irrigation_core.cloud import TuyaCloud
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.sync import sync_sensor_data as core_sync
from tuya_irrigation_core.sync import sync_single_sensor


class SyncService:
    """Orchestrates sensor data synchronization from Tuya Cloud."""

    def __init__(self, repo: IrrigationRepository, cloud: TuyaCloud | None):
        self._repo = repo
        self._cloud = cloud

    def sync_all_sensors(self, hours: int = 24) -> dict:
        """Sync all sensor data from Tuya Cloud. Returns stats dict."""
        if self._cloud is None:
            return {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": ["No cloud connection"]}
        return core_sync(self._repo, self._cloud, hours=hours)

    def sync_and_read_sensors(self, cluster_id: int) -> dict | None:
        """Sync cluster sensors and return live reading from first sensor."""
        if self._cloud is None:
            return None
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        if not sensors:
            return None
        try:
            for sensor in sensors:
                try:
                    sync_single_sensor(self._repo, self._cloud, sensor, hours=6)
                except Exception:
                    pass
            self._repo.session.flush()
            live = self._cloud.get_live_reading(sensors[0].tuya_device_id)
            return live if live else None
        except Exception:
            return None
