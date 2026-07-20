"""Sensor sync orchestration service."""

import logging
import time

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.gateway import DeviceGateway
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.sync import sync_sensor_data as core_sync
from greenhouse_core.sync import sync_single_sensor

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates sensor data synchronization from the Tuya Cloud gateway.

    The sync job is the **sole** Cloud writer of sensor readings. Every other
    consumer (health monitor, irrigation pipeline) reads the persisted rows;
    the only Cloud escape hatch here is :meth:`ensure_fresh_and_read`, which
    forces a single targeted sync when a reading is too stale to actuate on.
    """

    def __init__(
        self,
        repo: IrrigationRepository,
        registry: DeviceRegistry | None,
        cloud: DeviceGateway | None,
    ):
        self._repo = repo
        self._registry = registry
        self._cloud = cloud

    def sync_all_sensors(self, hours: int = 24) -> dict:
        """Sync all sensor data from the Cloud gateway. Returns stats dict."""
        if self._cloud is None:
            return {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": ["No cloud connection"]}
        return core_sync(self._repo, self._cloud, hours=hours)

    def ensure_fresh_and_read(self, cluster_id: int) -> dict | None:
        """Return the cluster's current sensor snapshot from SQLite.

        Reads the latest persisted reading for each sensor (no Cloud call). If
        any sensor's row is missing or older than ``SENSOR_READING_STALE_SECONDS``,
        forces **one** targeted sync for those sensors only — never the old
        "live-read every sensor twice" burst — then re-reads. Returns the first
        sensor's canonical values (temperature/soil/env_humidity/light) for the
        irrigation pipeline, or ``None`` when the cluster has no readable data.
        """
        sensors = self._repo.get_sensors_in_cluster(cluster_id)
        if not sensors:
            return None

        now = int(time.time())
        latest = {s.id: self._repo.get_latest_reading(s.id) for s in sensors}
        stale = [
            s for s in sensors if latest[s.id] is None or now - latest[s.id].timestamp > SENSOR_READING_STALE_SECONDS
        ]
        if stale and self._cloud is not None:
            for sensor in stale:
                try:
                    sync_single_sensor(self._repo, self._cloud, sensor, hours=6)
                except Exception:
                    logger.debug("Freshness sync failed for sensor %s", sensor.name, exc_info=True)
            self._repo.session.flush()
            latest = {s.id: self._repo.get_latest_reading(s.id) for s in sensors}

        row = latest.get(sensors[0].id)
        if row is None:
            return None
        return {
            "temperature": row.temperature,
            "soil_moisture": row.soil_moisture,
            "env_humidity": row.env_humidity,
            "light": row.light,
        }
