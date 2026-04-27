"""System-wide health pulse: sensor freshness, irrigator inventory, scheduler state."""

import time

from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.schemas import SystemHealthDevice, SystemHealthResponse
from tuya_irrigation_server.scheduler import scheduler
from tuya_irrigation_server.services.sync import SyncService

_STALE_SECONDS = 3 * 3600
_COLD_SECONDS = 24 * 3600
_FRESH_SECONDS = 3600
_DEVICE_LIMIT = 20


class SystemHealthService:
    """Derive a real-time health pulse from stored sensor data and scheduler state."""

    def __init__(self, repo: IrrigationRepository, sync_service: SyncService):
        self._repo = repo
        self._sync = sync_service

    def pulse(self) -> SystemHealthResponse:
        """Compute the system health pulse.

        Returns:
            SystemHealthResponse with sensor freshness counts, irrigator inventory,
            cloud reachability inference, and top-20 device statuses.
        """
        now = int(time.time())
        sensors = self._repo.list_all_sensors()
        irrigators = self._repo.list_all_irrigators()

        sensor_devices: list[SystemHealthDevice] = []
        last_ts_values: list[int] = []
        stale_count = 0

        for sensor in sensors:
            last_ts = self._repo.get_last_reading_timestamp(sensor.id)
            if last_ts:
                last_ts_values.append(last_ts)
            age = (now - last_ts) if last_ts else None
            if age is None or age > _STALE_SECONDS:
                stale_count += 1
                status = "cold" if (age is None or age > _COLD_SECONDS) else "stale"
            else:
                status = "ok"
            sensor_devices.append(SystemHealthDevice(id=sensor.id, name=sensor.name, status=status, age_seconds=age))

        irrigator_devices: list[SystemHealthDevice] = [
            SystemHealthDevice(id=irr.id, name=irr.name, status="ok", age_seconds=None) for irr in irrigators
        ]

        last_sync_at = max(last_ts_values) if last_ts_values else None
        cloud_reachable = any(ts > now - _FRESH_SECONDS for ts in last_ts_values) if last_ts_values else False

        open_alerts = self._repo.count_open_alerts()

        if not cloud_reachable:
            status = "down"
        elif stale_count > 0 or open_alerts >= 3:
            status = "degraded"
        else:
            status = "ok"

        all_devices = (sensor_devices + irrigator_devices)[:_DEVICE_LIMIT]

        return SystemHealthResponse(
            status=status,
            scheduler_running=scheduler.running,
            cloud_reachable=cloud_reachable,
            last_sync_at=last_sync_at,
            sensors_total=len(sensors),
            sensors_stale=stale_count,
            sensors_fresh=len(sensors) - stale_count,
            irrigators_total=len(irrigators),
            open_alerts=open_alerts,
            devices=all_devices,
        )
