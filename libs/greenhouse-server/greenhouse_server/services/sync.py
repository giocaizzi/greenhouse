"""Sensor sync orchestration service."""

import logging
import statistics
import time

from greenhouse_core.constants import SENSOR_READING_STALE_SECONDS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.gateway import DeviceGateway
from greenhouse_core.logic.cleaning import clean_readings_desc
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.sync import sync_sensor_data as core_sync
from greenhouse_core.sync import sync_single_sensor

logger = logging.getLogger(__name__)

# Look-back for the cluster snapshot's per-sensor values. Matches the decision
# engine's own window so both read the same slice of history.
SNAPSHOT_LOOKBACK_HOURS = 24


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
        "live-read every sensor twice" burst — then re-reads. Returns the
        cluster-level aggregate built by :meth:`_cluster_snapshot`
        (temperature/soil/env_humidity/light), or ``None`` when the cluster has
        no readable data.
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
            # Flush so the freshly-synced rows are visible to the snapshot query.
            self._repo.session.flush()

        return self._cluster_snapshot(sensors)

    def _cluster_snapshot(self, sensors) -> dict | None:
        """Fold the cluster's sensors into the single reading the pipeline acts on.

        One value per metric, aggregated the way the metric is used rather than
        taken from whichever sensor sorted first — in a two-plant cluster that
        made the number attributed to the cycle a coin flip between the plants
        (issue #103):

        - ``soil_moisture`` → **minimum**: the driest plant drives the call
          (invariant #2), so the aggregate must agree with the engine's rule.
        - ``temperature`` / ``env_humidity`` → mean of the sensors reporting.
        - ``light`` → maximum: the brightest spot in the cluster.

        Each sensor contributes its newest value **per metric** from the
        cleaned view, so a spike sitting on the latest row cannot become the
        cluster's current state.

        Args:
            sensors: The cluster's sensors.

        Returns:
            The aggregated snapshot, or ``None`` when no sensor has usable data.
        """
        temperatures: list[float] = []
        humidities: list[float] = []
        soils: list[float] = []
        lights: list[int] = []

        for sensor in sensors:
            readings = clean_readings_desc(self._repo.get_recent_readings(sensor.id, hours=SNAPSHOT_LOOKBACK_HOURS))
            for field, bucket in (
                ("temperature", temperatures),
                ("env_humidity", humidities),
                ("soil_moisture", soils),
                ("light", lights),
            ):
                value = next((getattr(r, field) for r in readings if getattr(r, field) is not None), None)
                if value is not None:
                    bucket.append(value)

        if not any((temperatures, humidities, soils, lights)):
            return None

        return {
            "temperature": statistics.mean(temperatures) if temperatures else None,
            "soil_moisture": min(soils) if soils else None,
            "env_humidity": statistics.mean(humidities) if humidities else None,
            "light": max(lights) if lights else None,
        }
