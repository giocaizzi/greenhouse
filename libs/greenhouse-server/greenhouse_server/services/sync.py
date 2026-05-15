"""Sensor sync orchestration service."""

import logging

from greenhouse_core.cloud import TuyaCloud
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.sync import sync_sensor_data as core_sync
from greenhouse_core.sync import sync_single_sensor

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates sensor data synchronization from Tuya Cloud."""

    def __init__(
        self,
        repo: IrrigationRepository,
        registry: DeviceRegistry | None,
        cloud: TuyaCloud | None,
    ):
        self._repo = repo
        self._registry = registry
        self._cloud = cloud

    def sync_all_sensors(self, hours: int = 24) -> dict:
        """Sync all sensor data from Tuya Cloud. Returns stats dict."""
        if self._cloud is None:
            return {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": ["No cloud connection"]}
        return core_sync(self._repo, self._cloud, hours=hours)

    def sync_and_read_sensors(self, cluster_id: int) -> dict | None:
        """Sync cluster sensors and return live reading from first sensor.

        Backfill of historical logs still goes through ``TuyaCloud`` (the
        Cloud API is the source of truth for sensor history). The live read
        is delegated to the per-sensor adapter resolved via
        :class:`DeviceRegistry`, which encapsulates the parser table.
        """
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
            return self._read_live(sensors[0])
        except Exception:
            return None

    def _read_live(self, sensor) -> dict | None:
        """Route the live read through the registry-resolved adapter.

        Unmapped sensors degrade silently — log a warning and return ``None``
        rather than blocking the cluster's irrigation pipeline.
        """
        if self._registry is None:
            return None
        adapter = self._registry.get_sensor(sensor)
        if adapter is None:
            logger.warning("No adapter for sensor %r — skipping live read", sensor.name)
            return None
        live = adapter.read_live(sensor)
        return live if live else None
