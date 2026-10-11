"""Sensor data sync logic.

Syncs sensor data from Tuya Cloud to local DB:
1. Pulls historical logs (getdevicelog) — backfills any gaps
2. Gets live reading (getstatus) — latest state
3. Deduplicates by (sensor_id, timestamp) — no duplicates ever

Tuya Cloud is the source of truth for sensor data.
Local DB is our permanent archive.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, TypedDict

from greenhouse_core.devices.gateway import DeviceGateway  # noqa: TC001 — pinned in golden imports.json
from greenhouse_core.repository import IrrigationRepository  # noqa: TC001 — pinned in golden imports.json

if TYPE_CHECKING:
    from greenhouse_core.models import Sensor

logger = logging.getLogger(__name__)

# Canonical measurement keys (the gateway's parsed names): a live read carrying at
# least one of them is a reading worth a row. Battery / water-warning alone are not.
_LIVE_MEASUREMENT_KEYS = ("temperature", "soil_moisture", "env_humidity", "light")


class SyncStats(TypedDict):
    """Totals of one sync run; the key order is the order API and web callers see."""

    total_synced: int
    total_new: int
    total_live: int
    errors: list[str]


def sync_sensor_data(db: IrrigationRepository, cloud: DeviceGateway, hours: int = 24) -> SyncStats:
    """Sync all sensor data from Tuya Cloud to local DB.

    Returns the run's totals across every cluster.
    """
    clusters = db.list_clusters()
    stats: SyncStats = {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}

    for cluster in clusters:
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        logger.info("[%s] Syncing %d sensor(s)...", cluster.name, len(sensors))

        for sensor in sensors:
            _sync_logged(db, cloud, sensor, hours, stats)

    return stats


def _sync_logged(db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor, hours: int, stats: SyncStats) -> None:
    """Sync one sensor into the running ``stats`` totals and log its summary.

    The per-sensor ``try`` is the isolation boundary: any failure is recorded as
    ``"<name>: <error>"``, logged at ERROR, and never stops the loop over sensors.
    """
    try:
        synced, new, live = sync_single_sensor(db, cloud, sensor, hours)
        stats["total_synced"] += synced
        stats["total_new"] += new
        stats["total_live"] += live

        logger.info("  %s: %s", sensor.name, _sync_summary(new, live))

    except Exception as e:  # noqa: BLE001 — per-sensor isolation: record, log, keep looping
        stats["errors"].append(f"{sensor.name}: {e}")
        # The log line is pinned (tests/test_contract_wp8_sync_gaps.py): `.exception` would need `e` dropped (TRY401).
        logger.error("  %s: %s", sensor.name, e)  # noqa: TRY400 — see above


def _sync_summary(new: int, live: int) -> str:
    """Return the per-sensor log summary, e.g. ``"2 new from logs, live ✓"``."""
    parts = []
    if new > 0:
        parts.append(f"{new} new from logs")
    if live:
        parts.append("live ✓")
    if not parts:
        parts.append("up to date")
    return ", ".join(parts)


def sync_single_sensor(
    db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor, hours: int
) -> tuple[int, int, int]:
    """Sync a single sensor. Returns (total_processed, new_inserted, live_saved).

    Order (invariant 8): sync window -> one ``getdevicelog`` pull -> per-reading
    insert -> exactly one best-effort live read.
    """
    since_ms = _sync_window_start(db.get_last_reading_timestamp(sensor.id), hours, time.time())
    logs = cloud.get_device_logs(sensor.tuya_device_id, since_ms=since_ms)
    grouped = cloud.group_logs_by_timestamp(logs)
    new_count = _store_history(db, sensor, grouped)
    live_saved = int(_store_live_reading(db, cloud, sensor))
    return len(grouped), new_count, live_saved


def _sync_window_start(last_ts: int | None, hours: int, now: float) -> int:
    """Return the ``getdevicelog`` window start in epoch milliseconds.

    With a stored reading, resume one minute before it (overlap for safety; the
    repository de-duplicates on ``(sensor_id, timestamp)``). On the first sync
    (no row, or a falsy timestamp) pull the full ``hours`` window back from ``now``.
    """
    if last_ts:
        return (last_ts - 60) * 1000
    return int((now - hours * 3600) * 1000)


def _store_history(db: IrrigationRepository, sensor: Sensor, grouped: list[dict[str, Any]]) -> int:
    """Insert each grouped log reading; return how many rows were new.

    Readings without a timestamp are skipped. ``water_warning`` (DP 111) is
    stored when the log carries it as a parsed boolean; a raw value the parser
    rejected is stored as unknown (``None``) rather than guessed.
    """
    new_count = 0
    for reading in grouped:
        ts = reading.get("timestamp")
        if not ts:
            continue

        result = db.add_sensor_reading(
            sensor_id=sensor.id,
            timestamp=ts,
            temperature=reading.get("temperature"),
            soil_moisture=reading.get("soil_moisture"),
            light=reading.get("light"),
            env_humidity=reading.get("env_humidity"),
            battery_state=reading.get("battery_state"),
            water_warning=_logged_bool(reading.get("water_warning")),
        )
        if result is not None:
            new_count += 1
    return new_count


def _logged_bool(value: object) -> bool | None:
    """Return a parsed boolean log value, or ``None`` for a raw value the DP parser rejected."""
    return value if isinstance(value, bool) else None


def _store_live_reading(db: IrrigationRepository, cloud: DeviceGateway, sensor: Sensor) -> bool:
    """Persist the sensor's current state at now; ``True`` when a new row was stored.

    The single live read is best-effort: any error is logged (WARNING, with the
    traceback) and counts as "not saved", so a flaky live endpoint never fails
    the backfill above.
    """
    saved = False
    try:
        live = cloud.get_live_reading(sensor.tuya_device_id)
        if live and any(k in live for k in _LIVE_MEASUREMENT_KEYS):
            now = int(time.time())
            result = db.add_sensor_reading(
                sensor_id=sensor.id,
                timestamp=now,
                temperature=live.get("temperature"),
                soil_moisture=live.get("soil_moisture"),
                light=live.get("light"),
                env_humidity=live.get("env_humidity"),
                battery_state=live.get("battery_state"),
                water_warning=live.get("water_warning"),
            )
            if result is not None:
                saved = True
    except Exception:  # noqa: BLE001 — the live read is best-effort by contract (invariant 8); logged with exc_info
        logger.warning("  %s: live read failed", sensor.name, exc_info=True)
    return saved
