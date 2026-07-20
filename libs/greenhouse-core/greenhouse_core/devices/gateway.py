"""Single Tuya device gateway — one Cloud client, cloud reads + local factory.

Merges the former ``TuyaCloud`` (sensor reads) and ``TuyaTransport``
(actuation + local-key lookup) into one boundary that owns exactly **one**
``tinytuya.Cloud`` instance, and therefore fetches **one** auth token. Sensor
data reads, irrigator commands, and the local ``OutletDevice`` factory all
funnel through here.

Steady-state Cloud budget is just the sync job's per-sensor reads. Local
actuation resolves each irrigator's ``local_key`` from its persisted ``config``
blob (or a process cache), so :meth:`DeviceGateway.open_local` never calls the
Cloud once a key is known — this is what removes the per-poll ``getdevices``
storm the pump watcher used to cause.

``tinytuya`` is imported here (and re-exported from ``greenhouse_core.devices``)
so tests patch ``greenhouse_core.devices.tinytuya.Cloud`` — which mutates the
shared ``tinytuya`` module object — and intercept construction here too.
"""

from __future__ import annotations

import json as _json
import logging
import os
import time
from collections.abc import Callable

import tinytuya

from greenhouse_core.models import Irrigator

logger = logging.getLogger(__name__)

LOCAL_TIMEOUT = 5

# Datapoint parsers — the single home for Tuya DP code → (canonical_key, value).
# TR-301Z (zwjcy — 土壤温湿度) soil temp/humidity sensor and friends.
DATAPOINT_PARSERS: dict[str, Callable[[object], tuple[str, object]]] = {
    # Standard DPs (v1 getstatus / getdevicelog)
    "temp_current": lambda v: ("temperature", float(v) / 10.0),
    "va_temperature": lambda v: ("temperature", float(v) / 10.0),
    "humidity": lambda v: ("soil_moisture", float(v)),
    "humidity_value": lambda v: ("soil_moisture", float(v)),
    "va_humidity": lambda v: ("env_humidity", float(v)),
    "battery_state": lambda v: ("battery_state", v),
    "battery_percentage": lambda v: ("battery_percentage", int(v)),
    "bright_value": lambda v: ("light", int(v)),
    "light": lambda v: ("light", int(v)),
    # Extended DPs (v2 shadow properties — DP 101+)
    "env_humidity": lambda v: ("env_humidity", float(v)),  # DP 101: ambient humidity %
    "illumiance": lambda v: ("light", int(v)),  # DP 102: lux (typo in Tuya API)
    "water_warning": lambda v: ("water_warning", bool(v)),  # DP 111: device soil-dry alert
    "soil_warning": lambda v: ("soil_warning", int(v)),  # DP 110: soil warning code
}


def _coerce_config(config: object) -> dict:
    """Return the irrigator ``config`` blob as a dict (JSON string or dict)."""
    if isinstance(config, dict):
        return config
    if isinstance(config, str):
        try:
            parsed = _json.loads(config)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def group_logs_by_timestamp(logs: list[dict], tolerance_ms: int = 5000) -> list[dict]:
    """Group log entries reported at ~the same time into single readings.

    Sensors often report multiple DPs within a few seconds; this collapses
    them into one reading dict keyed by the parsed canonical field names.
    """
    if not logs:
        return []

    readings = []
    current_group = {"timestamp": logs[0]["timestamp"]}
    current_ts = logs[0]["timestamp_ms"]

    for log in logs:
        if abs(log["timestamp_ms"] - current_ts) > tolerance_ms:
            readings.append(current_group)
            current_group = {"timestamp": log["timestamp"]}
            current_ts = log["timestamp_ms"]

        if "key" in log:
            current_group[log["key"]] = log["value"]
            current_group["timestamp"] = log["timestamp"]

    if len(current_group) > 1:  # Has at least timestamp + one value
        readings.append(current_group)

    return readings


class DeviceGateway:
    """One Tuya boundary: a single ``tinytuya.Cloud`` plus a local-device factory.

    All adapters, the sync service, and the scheduler jobs share one instance
    (built once at app startup, stored on ``app.state.device_gateway``). Nothing
    downstream constructs its own client, so the whole process holds one token.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        region: str | None = None,
        *,
        raw: object | None = None,
        on_key_discovered: Callable[[str, str], None] | None = None,
    ):
        self.client_id = client_id or os.environ.get("TUYA_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = region or os.environ.get("TUYA_REGION", "eu")

        if not self.client_id or not self.client_secret:
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET")

        # ``raw`` lets callers inject an already-constructed client so the whole
        # app shares one token; omitted, we build the single owned instance.
        self._cloud = (
            raw
            if raw is not None
            else tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.client_id,
                apiSecret=self.client_secret,
            )
        )
        # device_id → local_key, warmed lazily on the first cold lookup and
        # reused for the lifetime of this (app-scoped) instance.
        self._key_cache: dict[str, str] = {}
        self._on_key_discovered = on_key_discovered

    # ── Sensor cloud reads ────────────────────────────────────────────────

    def get_live_reading(self, device_id: str) -> dict:
        """Get current sensor values via the v2.0 shadow properties endpoint.

        Uses ``/v2.0/cloud/thing/{id}/shadow/properties`` which exposes the
        full DP set (including extended DPs env_humidity/lux/water_warning not
        surfaced by v1.0 getstatus). The v1.0 ``getstatus`` fallback fires
        **only** when the v2.0 endpoint genuinely fails (transport error or
        ``success=false``) — never merely because the parsed set is empty, so a
        healthy device is read exactly once.

        Returns a dict of parsed canonical values (``temperature``,
        ``soil_moisture``, ``env_humidity``, ``light``, ``battery_state``,
        ``water_warning``…). An empty dict means the live DP set held nothing we
        recognise — that is authoritative, not a reason to re-read.
        """
        result = None
        try:
            result = self._cloud.cloudrequest(f"/v2.0/cloud/thing/{device_id}/shadow/properties")
        except Exception:
            result = None

        if result is not None and result.get("success"):
            data: dict = {}
            for prop in result.get("result", {}).get("properties", []):
                parser = DATAPOINT_PARSERS.get(prop.get("code", ""))
                if parser:
                    key, value = parser(prop.get("value"))
                    data[key] = value
            return data

        # Deliberate fallback: the v2.0 endpoint is unreachable/unsupported for
        # this device. Pay the second call only here, not on empty-but-ok reads.
        result = self._cloud.getstatus(device_id)
        if not result.get("success"):
            raise RuntimeError(f"Cloud API error: {result}")

        data = {}
        for dp in result.get("result", []):
            parser = DATAPOINT_PARSERS.get(dp.get("code", ""))
            if parser:
                key, value = parser(dp.get("value"))
                data[key] = value
        return data

    def get_device_logs(
        self,
        device_id: str,
        since_ms: int | None = None,
        hours: int = 24,
        max_records: int = 100,
    ) -> list[dict]:
        """Get historical sensor reports (``getdevicelog``), parsed + sorted.

        Each dict carries ``timestamp_ms``/``timestamp``/``code``/``raw_value``
        and, for recognised DP codes, a parsed ``key``/``value``. Results are
        sorted oldest-first so :func:`group_logs_by_timestamp` can fold them.
        """
        if since_ms is None:
            since_ms = int((time.time() - hours * 3600) * 1000)

        result = self._cloud.getdevicelog(
            device_id,
            start=since_ms,
            end=0,
            evtype=7,  # Status report events
            size=max_records,
        )

        logs = result.get("result", {}).get("logs", [])
        parsed = []

        for log in logs:
            code = log.get("code", "")
            raw_value = log.get("value", "")
            event_time_ms = log.get("event_time", 0)

            entry = {
                "timestamp_ms": event_time_ms,
                "timestamp": event_time_ms // 1000,
                "code": code,
                "raw_value": raw_value,
            }

            parser = DATAPOINT_PARSERS.get(code)
            if parser:
                try:
                    key, value = parser(raw_value)
                    entry["key"] = key
                    entry["value"] = value
                except (ValueError, TypeError):
                    entry["key"] = code
                    entry["value"] = raw_value
            else:
                entry["key"] = code
                entry["value"] = raw_value

            parsed.append(entry)

        parsed.sort(key=lambda x: x["timestamp_ms"])
        return parsed

    @staticmethod
    def group_logs_by_timestamp(logs: list[dict], tolerance_ms: int = 5000) -> list[dict]:
        """Fold parsed log entries into readings (see module function)."""
        return group_logs_by_timestamp(logs, tolerance_ms)

    # ── Irrigator cloud commands ──────────────────────────────────────────

    def send_command(self, device_id: str, commands: dict) -> tuple[bool, str]:
        """Send a Cloud-API command payload (shaped as ``{"commands": [...]}``)."""
        result = self._cloud.sendcommand(device_id, commands)
        if result.get("success"):
            return True, "Command succeeded"
        return False, f"Cloud API error: {result}"

    def get_status(self, device_id: str) -> dict:
        """Return the raw Cloud-API ``getstatus`` response."""
        return self._cloud.getstatus(device_id)

    # ── Local factory (cached key, Cloud only on a cold miss) ─────────────

    def resolve_local_key(
        self,
        device_id: str,
        config: object | None = None,
        *,
        refresh: bool = False,
    ) -> str | None:
        """Resolve a device's ``local_key`` cheaply.

        Order: persisted ``config['local_key']`` → process cache → (cold path)
        one Cloud ``getdevices`` lookup, whose result is cached and handed to
        the ``on_key_discovered`` persistence hook. ``refresh=True`` skips the
        cheap sources and forces the Cloud lookup (stale-key recovery).
        """
        if not refresh:
            cfg_key = _coerce_config(config).get("local_key") if config is not None else None
            if cfg_key:
                return cfg_key
            cached = self._key_cache.get(device_id)
            if cached:
                return cached

        key = self._lookup_key_via_cloud(device_id)
        if key:
            self._key_cache[device_id] = key
            if self._on_key_discovered is not None:
                try:
                    self._on_key_discovered(device_id, key)
                except Exception:
                    logger.exception("local_key persistence hook failed for %s", device_id)
        return key

    def _lookup_key_via_cloud(self, device_id: str) -> str | None:
        """The one place that calls Cloud ``getdevices`` to find a local key."""
        devices = self._cloud.getdevices()
        if isinstance(devices, list):
            for d in devices:
                if d.get("id") == device_id:
                    return d.get("key")
        return None

    def invalidate_key(self, device_id: str) -> None:
        """Drop a cached local_key (call on key rotation / local-auth failure)."""
        self._key_cache.pop(device_id, None)

    def open_local(
        self,
        irrigator: Irrigator,
        protocol_version: float,
        *,
        refresh: bool = False,
    ) -> tinytuya.OutletDevice:
        """Build a connected ``OutletDevice`` for ``irrigator``.

        Resolves ``local_key`` via :meth:`resolve_local_key` (Cloud-free in
        steady state) and ``device_ip`` from the irrigator's ``config`` blob.
        Raises ``ConnectionError`` when either is missing. ``refresh=True``
        forces a fresh Cloud key lookup for stale-key recovery.
        """
        config = _coerce_config(irrigator.config)
        local_ip = config.get("device_ip")
        if not local_ip:
            raise ConnectionError(f"No device_ip in config for irrigator {irrigator.tuya_device_id}.")

        local_key = self.resolve_local_key(irrigator.tuya_device_id, config, refresh=refresh)
        if not local_key:
            raise ConnectionError(f"Could not find local key for device {irrigator.tuya_device_id}")

        device = tinytuya.OutletDevice(irrigator.tuya_device_id, local_ip, local_key)
        device.set_version(protocol_version)
        device.set_socketTimeout(LOCAL_TIMEOUT)
        return device


__all__ = ["DATAPOINT_PARSERS", "DeviceGateway", "group_logs_by_timestamp"]
