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

import logging
import os
import time
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

import tinytuya

from greenhouse_core.constants import SENSOR_LOG_MAX_PAGES, SENSOR_LOG_PAGE_SIZE
from greenhouse_core.devices.datapoints import DATAPOINT_PARSERS, group_logs_by_timestamp
from greenhouse_core.models import Irrigator, parse_device_config

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

LOCAL_TIMEOUT = 5


class _CloudDevice(TypedDict, total=False):
    """The fields of a Cloud ``getdevices`` entry the key lookup reads (the payload carries more)."""

    id: str
    key: str


class _TuyaCloudClient(Protocol):
    """The slice of ``tinytuya.Cloud`` the gateway calls (an injected ``raw`` client must match it).

    Responses are raw Cloud JSON, hence ``dict[str, Any]``.
    """

    def cloudrequest(self, url: str, /) -> dict[str, Any]: ...

    def getstatus(self, deviceid: str, /) -> dict[str, Any]: ...

    def getdevicelog(  # noqa: PLR0913 — mirrors tinytuya.Cloud.getdevicelog's keyword API
        self,
        deviceid: str,
        /,
        *,
        start: int,
        end: int,
        evtype: int,
        size: int,
        max_fetches: int,
        start_row_key: str | None,
    ) -> dict[str, Any]: ...

    def sendcommand(self, deviceid: str, commands: dict[str, Any], /) -> dict[str, Any]: ...

    def getdevices(self) -> list[_CloudDevice] | dict[str, Any]: ...


def _parse_datapoints(items: list[dict[str, Any]], device_id: str) -> dict[str, Any]:
    """Parse live DP items (``{"code", "value"}``) into canonical keys, one DP at a time.

    Unknown codes are ignored. A value its parser rejects (``ValueError`` /
    ``TypeError`` — e.g. a ``None`` humidity) is logged and skipped so one bad
    datapoint cannot discard the rest of the reading. When two codes map to the
    same canonical key, the later one wins.
    """
    data: dict[str, Any] = {}
    for item in items:
        code = item.get("code", "")
        parser = DATAPOINT_PARSERS.get(code)
        if parser is None:
            continue
        try:
            key, value = parser(item.get("value"))
        except (ValueError, TypeError):
            logger.warning("Skipping unparseable DP %s=%r from %s", code, item.get("value"), device_id, exc_info=True)
            continue
        data[key] = value
    return data


def _parse_log_entry(log: dict[str, Any]) -> dict[str, Any]:
    """Shape one raw ``getdevicelog`` entry; a value its parser rejects keeps the raw code/value."""
    code = log.get("code", "")
    raw_value = log.get("value", "")
    event_time_ms = log.get("event_time", 0)
    entry: dict[str, Any] = {
        "timestamp_ms": event_time_ms,
        "timestamp": event_time_ms // 1000,
        "code": code,
        "raw_value": raw_value,
        "key": code,
        "value": raw_value,
    }
    parser = DATAPOINT_PARSERS.get(code)
    if parser is not None:
        try:
            entry["key"], entry["value"] = parser(raw_value)
        except (ValueError, TypeError):
            logger.debug("Keeping raw log value for DP %s=%r", code, raw_value, exc_info=True)
    return entry


def _next_row_key(device_id: str, result: dict[str, Any], previous: str | None) -> str | None:
    """Return the cursor for the next ``getdevicelog`` page, or ``None`` to stop paging.

    Stops when the page failed (``success: false``, logged), when the server
    reports no more rows or no cursor, and when the cursor repeats (logged) —
    a repeated cursor would re-read the same page until the page cap.
    """
    if result.get("success") is False:
        logger.warning("getdevicelog page for %s failed: %s", device_id, result)
        return None
    page = result.get("result") or {}
    key: str | None = page.get("next_row_key")
    if not page.get("has_next") or not key:
        return None
    if key == previous:
        logger.warning("getdevicelog for %s repeated cursor %r; stopping", device_id, key)
        return None
    return key


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
        raw: _TuyaCloudClient | None = None,
        on_key_discovered: Callable[[str, str], None] | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("TUYA_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = region or os.environ.get("TUYA_REGION", "eu")

        if not self.client_id or not self.client_secret:
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET")

        # ``raw`` lets callers inject an already-constructed client so the whole
        # app shares one token; omitted, we build the single owned instance.
        self._cloud: _TuyaCloudClient = (
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

    def get_live_reading(self, device_id: str) -> dict[str, Any]:
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
        recognise — that is authoritative, not a reason to re-read. A DP whose
        value its parser rejects is logged and skipped; the other DPs of the
        reading are kept.
        """
        result: dict[str, Any] | None = None
        try:
            result = self._cloud.cloudrequest(f"/v2.0/cloud/thing/{device_id}/shadow/properties")
        except Exception:
            result = None

        if result is not None and result.get("success"):
            return _parse_datapoints(result.get("result", {}).get("properties", []), device_id)

        # Deliberate fallback: the v2.0 endpoint is unreachable/unsupported for
        # this device. Pay the second call only here, not on empty-but-ok reads.
        result = self._cloud.getstatus(device_id)
        if not result.get("success"):
            raise RuntimeError(f"Cloud API error: {result}")
        return _parse_datapoints(result.get("result", []), device_id)

    def get_device_logs(
        self,
        device_id: str,
        since_ms: int | None = None,
        hours: int = 24,
        page_size: int = SENSOR_LOG_PAGE_SIZE,
        max_pages: int = SENSOR_LOG_MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """Get historical sensor reports (``getdevicelog``), parsed + sorted.

        Pages through the window with Tuya's ``next_row_key`` cursor until the
        server reports no more rows, so a long gap is no longer cut at one
        page of ``page_size`` entries. ``max_pages`` bounds the Cloud cost of
        one sensor's sync; hitting it is logged as a WARNING.

        Each dict carries ``timestamp_ms``/``timestamp``/``code``/``raw_value``
        and, for recognised DP codes, a parsed ``key``/``value``. Results are
        sorted oldest-first so :func:`group_logs_by_timestamp` can fold them.
        """
        if since_ms is None:
            since_ms = int((time.time() - hours * 3600) * 1000)

        logs = self._fetch_log_pages(device_id, since_ms, page_size, max_pages)
        parsed = [_parse_log_entry(log) for log in logs]
        parsed.sort(key=lambda x: x["timestamp_ms"])
        return parsed

    def _fetch_log_pages(self, device_id: str, since_ms: int, page_size: int, max_pages: int) -> list[dict[str, Any]]:
        """Collect raw ``getdevicelog`` entries page by page (one request per page).

        ``max_fetches=1`` keeps tinytuya from paginating on its own, so every
        Cloud request is issued (and countable) here. The window end is fixed
        once, so every page of one cursor queries the same window.
        """
        end_ms = int(time.time() * 1000)
        logs: list[dict[str, Any]] = []
        row_key: str | None = None
        for _ in range(max_pages):
            result = self._cloud.getdevicelog(
                device_id,
                start=since_ms,
                end=end_ms,
                evtype=7,  # Status report events
                size=page_size,
                max_fetches=1,
                start_row_key=row_key,
            )
            page = result.get("result") or {}
            logs.extend(page.get("logs", []))
            next_key = _next_row_key(device_id, result, row_key)
            if next_key is None:
                return logs
            row_key = next_key
        logger.warning(
            "getdevicelog for %s still had rows after %d pages (%d entries); the window is not fully covered",
            device_id,
            max_pages,
            len(logs),
        )
        return logs

    @staticmethod
    def group_logs_by_timestamp(logs: list[dict[str, Any]], tolerance_ms: int = 5000) -> list[dict[str, Any]]:
        """Fold parsed log entries into readings (see module function)."""
        return group_logs_by_timestamp(logs, tolerance_ms)

    # ── Irrigator cloud commands ──────────────────────────────────────────

    def send_command(self, device_id: str, commands: dict[str, Any]) -> tuple[bool, str]:
        """Send a Cloud-API command payload (shaped as ``{"commands": [...]}``)."""
        result = self._cloud.sendcommand(device_id, commands)
        if result.get("success"):
            return True, "Command succeeded"
        return False, f"Cloud API error: {result}"

    def get_status(self, device_id: str) -> dict[str, Any]:
        """Return the raw Cloud-API ``getstatus`` response."""
        return self._cloud.getstatus(device_id)

    # ── Local factory (cached key, Cloud only on a cold miss) ─────────────

    def resolve_local_key(self, device_id: str, config: object | None = None) -> str | None:
        """Resolve a device's ``local_key`` cheaply.

        Order: persisted ``config['local_key']`` → process cache → (cold path)
        one Cloud ``getdevices`` lookup, whose result is cached and handed to
        the ``on_key_discovered`` persistence hook.
        """
        cfg_key: str | None = parse_device_config(config).get("local_key") if config is not None else None
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

    def open_local(self, irrigator: Irrigator, protocol_version: float | None) -> tinytuya.OutletDevice:
        """Build a connected ``OutletDevice`` for ``irrigator``.

        Resolves ``local_key`` via :meth:`resolve_local_key` (Cloud-free in
        steady state) and ``device_ip`` from the irrigator's ``config`` blob.
        Raises ``ConnectionError`` when either is missing.
        """
        config = parse_device_config(irrigator.config)
        local_ip = config.get("device_ip")
        if not local_ip:
            raise ConnectionError(f"No device_ip in config for irrigator {irrigator.tuya_device_id}.")

        local_key = self.resolve_local_key(irrigator.tuya_device_id, config)
        if not local_key:
            raise ConnectionError(f"Could not find local key for device {irrigator.tuya_device_id}")

        device = tinytuya.OutletDevice(irrigator.tuya_device_id, local_ip, local_key)
        device.set_version(protocol_version)
        device.set_socketTimeout(LOCAL_TIMEOUT)
        return device


__all__ = ["DATAPOINT_PARSERS", "DeviceGateway", "group_logs_by_timestamp"]
