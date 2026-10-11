"""Tuya datapoint parsing — the single home for DP code → (canonical_key, value).

Pure functions and tables, no I/O: :data:`DATAPOINT_PARSERS` (used by the
gateway's live and log reads and by the sensor profiles) and
:func:`group_logs_by_timestamp` (folds ``getdevicelog`` entries into readings).
Both are re-exported from :mod:`greenhouse_core.devices.gateway`, their
historical import path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_BOOL_DP_TEXT = {"true": True, "1": True, "false": False, "0": False}


def _parse_bool_dp(value: object) -> bool:
    """Parse a Tuya boolean DP strictly.

    The v2 shadow reports booleans as ``true``/``false`` (or ``0``/``1``), but
    ``getdevicelog`` reports every value as a string, so a plain ``bool(v)``
    would read the logged ``"false"`` as ``True``. Anything that is not a clear
    boolean raises ``ValueError`` so the caller skips the DP instead of
    guessing.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _BOOL_DP_TEXT:
        return _BOOL_DP_TEXT[value.strip().lower()]
    msg = f"not a boolean DP value: {value!r}"
    raise ValueError(msg)


# Datapoint parsers — the single home for Tuya DP code → (canonical_key, value).
# TR-301Z (zwjcy — 土壤温湿度) soil temp/humidity sensor and friends.
DATAPOINT_PARSERS: dict[str, Callable[[Any], tuple[str, object]]] = {
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
    "water_warning": lambda v: ("water_warning", _parse_bool_dp(v)),  # DP 111: device soil-dry alert
    "soil_warning": lambda v: ("soil_warning", int(v)),  # DP 110: soil warning code
}


def group_logs_by_timestamp(logs: list[dict[str, Any]], tolerance_ms: int = 5000) -> list[dict[str, Any]]:
    """Group log entries reported at ~the same time into single readings.

    Sensors often report multiple DPs within a few seconds; this collapses
    them into one reading dict keyed by the parsed canonical field names.
    """
    if not logs:
        return []

    readings: list[dict[str, Any]] = []
    current_group: dict[str, Any] = {"timestamp": logs[0]["timestamp"]}
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


__all__ = ["DATAPOINT_PARSERS", "group_logs_by_timestamp"]
