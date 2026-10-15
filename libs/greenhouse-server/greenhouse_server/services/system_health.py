"""System-wide health pulse: sensor freshness, irrigator inventory, scheduler state."""

import time
from typing import TYPE_CHECKING

from greenhouse_core.constants import (
    SYSTEM_HEALTH_COLD_SECONDS,
    SYSTEM_HEALTH_DEGRADED_OPEN_ALERTS,
    SYSTEM_HEALTH_DEVICE_LIMIT,
    SYSTEM_HEALTH_FAILING_STARTS,
    SYSTEM_HEALTH_FAILURE_LOOKBACK_SECONDS,
    SYSTEM_HEALTH_FRESH_SECONDS,
    SYSTEM_HEALTH_STALE_SECONDS,
)
from greenhouse_core.devices.health import HealthAlarm
from greenhouse_core.models import EVENT_ACTION_ATTEMPTED, EVENT_ACTION_START, SOURCE_IRRIGATION
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import SystemHealthDevice, SystemHealthResponse
from greenhouse_server.scheduler import scheduler
from greenhouse_server.services.alerts import find_open_alert
from greenhouse_server.services.sync import SyncService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from greenhouse_core.models import IrrigationEvent, Irrigator, Sensor
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor


class SystemHealthService:
    """Derive a real-time health pulse from stored sensor data and scheduler state."""

    def __init__(
        self,
        repo: IrrigationRepository,
        sync_service: SyncService,
        *,
        health_monitor: "DeviceHealthMonitor | None" = None,
    ) -> None:
        """Bind the pulse to the repository, the sync service and the device-health monitor.

        Without a monitor (no Tuya credentials, or a test) no irrigator reads ``offline``.
        """
        self._repo = repo
        self._sync = sync_service
        self._health_monitor = health_monitor

    def pulse(self) -> SystemHealthResponse:
        """Compute the system health pulse.

        Returns:
            SystemHealthResponse with sensor freshness counts, irrigator inventory,
            cloud reachability inference, and up to ``SYSTEM_HEALTH_DEVICE_LIMIT`` device
            statuses, devices that are not ``ok`` first, irrigators before sensors.
        """
        now = int(time.time())
        sensors = self._repo.list_all_sensors()
        irrigators = self._repo.list_all_irrigators()

        sensor_devices, last_ts_values, stale_count = self._sensor_devices(sensors, now)

        irrigator_devices = [self._irrigator_device(irr, now) for irr in irrigators]
        failing = sum(1 for dev in irrigator_devices if dev.status == "failing")
        offline = sum(1 for dev in irrigator_devices if dev.status == "offline")

        last_sync_at = max(last_ts_values) if last_ts_values else None
        cloud_reachable = (
            any(ts > now - SYSTEM_HEALTH_FRESH_SECONDS for ts in last_ts_values) if last_ts_values else False
        )

        open_alerts = self._repo.count_open_alerts()

        status = _overall_status(
            cloud_reachable=cloud_reachable, stale_count=stale_count, open_alerts=open_alerts, failing=failing + offline
        )

        # A stable sort keeps irrigators ahead of sensors, so a problem device is never cut off.
        all_devices = sorted(irrigator_devices + sensor_devices, key=lambda dev: dev.status == "ok")

        return SystemHealthResponse(
            status=status,
            scheduler_running=scheduler.running,
            cloud_reachable=cloud_reachable,
            last_sync_at=last_sync_at,
            sensors_total=len(sensors),
            sensors_stale=stale_count,
            sensors_fresh=len(sensors) - stale_count,
            irrigators_total=len(irrigators),
            irrigators_failing=failing,
            irrigators_offline=offline,
            open_alerts=open_alerts,
            devices=all_devices[:SYSTEM_HEALTH_DEVICE_LIMIT],
        )

    def _irrigator_device(self, irrigator: "Irrigator", now: int) -> SystemHealthDevice:
        """An irrigator's row: ``offline`` per the health monitor, else ``failing`` or ``ok``.

        Offline is the monitor's cached ``DEVICE_OFFLINE`` alarm (the one that gates actuation;
        no live read here). Failing means ``SYSTEM_HEALTH_FAILING_STARTS`` failed starts in a row
        (invariant 12: ``attempted`` events), or at least one while the cluster's
        ``actuation_failed`` alert is open; a successful ``start`` resets it.
        """
        since = now - SYSTEM_HEALTH_FAILURE_LOOKBACK_SECONDS
        failures = _consecutive_failures(self._repo.list_events_since(irrigator.id, since))
        alert_open = (
            find_open_alert(
                self._repo, source=SOURCE_IRRIGATION, code="actuation_failed", cluster_id=irrigator.cluster_id
            )
            is not None
        )
        failing = failures >= SYSTEM_HEALTH_FAILING_STARTS or (alert_open and failures > 0)
        status = "failing" if failing else "ok"
        if self._is_offline(irrigator):
            status = "offline"
        return SystemHealthDevice(
            id=irrigator.id,
            name=irrigator.name,
            status=status,
            note=f"{failures} failed start(s) in a row" if failures else None,
            consecutive_failures=failures,
        )

    def _is_offline(self, irrigator: "Irrigator") -> bool:
        """Whether the health monitor's cached state holds a ``DEVICE_OFFLINE`` alarm for the irrigator."""
        if self._health_monitor is None:
            return False
        _, alarms = self._health_monitor.is_actuation_blocked(irrigator)
        return HealthAlarm.DEVICE_OFFLINE in alarms

    def _sensor_devices(self, sensors: "Sequence[Sensor]", now: int) -> tuple[list[SystemHealthDevice], list[int], int]:
        """Per-sensor freshness rows, the sensors' last reading timestamps, and how many are stale or cold."""
        sensor_devices: list[SystemHealthDevice] = []
        last_ts_values: list[int] = []
        stale_count = 0

        for sensor in sensors:
            last_ts = self._repo.get_last_reading_timestamp(sensor.id)
            if last_ts:
                last_ts_values.append(last_ts)
            age = (now - last_ts) if last_ts else None
            if age is None or age > SYSTEM_HEALTH_STALE_SECONDS:
                stale_count += 1
                status = "cold" if (age is None or age > SYSTEM_HEALTH_COLD_SECONDS) else "stale"
            else:
                status = "ok"
            sensor_devices.append(SystemHealthDevice(id=sensor.id, name=sensor.name, status=status, age_seconds=age))

        return sensor_devices, last_ts_values, stale_count


def _consecutive_failures(events: "Sequence[IrrigationEvent]") -> int:
    """How many ``attempted`` events follow the newest ``start`` (other actions are ignored)."""
    count = 0
    for event in sorted(events, key=lambda e: (e.timestamp, e.id), reverse=True):
        if event.action == EVENT_ACTION_START:
            break
        if event.action == EVENT_ACTION_ATTEMPTED:
            count += 1
    return count


def _overall_status(*, cloud_reachable: bool, stale_count: int, open_alerts: int, failing: int) -> str:
    """Down without fresh cloud data; degraded with stale sensors, a failing irrigator or 3+ open alerts."""
    if not cloud_reachable:
        return "down"
    if stale_count > 0 or failing > 0 or open_alerts >= SYSTEM_HEALTH_DEGRADED_OPEN_ALERTS:
        return "degraded"
    return "ok"
