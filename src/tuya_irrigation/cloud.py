#!/usr/bin/env python3
"""Tuya Cloud API client for sensor data.

Centralizes all Tuya Cloud communication:
- Live readings (getstatus)
- Historical logs (getdevicelog)
- Datapoint parsing for known sensor types
"""

import os
import time

import tinytuya

# Known datapoint parsers by sensor category
# TR-301Z (zwjcy - 土壤温湿度) soil temp/humidity sensor
DATAPOINT_PARSERS = {
    "temp_current": lambda v: ("temperature", float(v) / 10.0),
    "va_temperature": lambda v: ("temperature", float(v) / 10.0),
    "humidity": lambda v: ("soil_moisture", float(v)),
    "humidity_value": lambda v: ("soil_moisture", float(v)),
    "va_humidity": lambda v: ("humidity", float(v)),
    "battery_state": lambda v: ("battery_state", v),
    "battery_percentage": lambda v: ("battery_percentage", int(v)),
    "bright_value": lambda v: ("light", int(v)),
    "light": lambda v: ("light", int(v)),
}


class TuyaCloud:
    """Tuya Cloud API client for reading sensor data."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        region: str | None = None,
    ):
        self.client_id = client_id or os.environ.get("TUYA_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = region or os.environ.get("TUYA_REGION", "eu")

        if not self.client_id or not self.client_secret:
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET")

        self._cloud = tinytuya.Cloud(
            apiRegion=self.region,
            apiKey=self.client_id,
            apiSecret=self.client_secret,
        )

    def get_live_reading(self, device_id: str) -> dict:
        """Get current sensor values via getstatus().

        Returns dict with parsed values:
        - temperature (°C)
        - soil_moisture (%)
        - humidity (%)
        - battery_state / battery_percentage
        - light (lux)
        """
        result = self._cloud.getstatus(device_id)

        if not result.get("success"):
            raise RuntimeError(f"Cloud API error: {result}")

        data = {}
        for dp in result.get("result", []):
            code = dp.get("code", "")
            value = dp.get("value")
            parser = DATAPOINT_PARSERS.get(code)
            if parser:
                key, parsed_value = parser(value)
                data[key] = parsed_value

        return data

    def get_device_logs(
        self,
        device_id: str,
        since_ms: int | None = None,
        hours: int = 24,
        max_records: int = 100,
    ) -> list[dict]:
        """Get historical sensor reports from Tuya Cloud.

        Each returned dict has:
        - timestamp_ms: event time in milliseconds
        - timestamp: event time in seconds (Unix)
        - code: datapoint code (e.g. 'temp_current', 'humidity')
        - raw_value: original string value from cloud
        - key: parsed key name (e.g. 'temperature', 'soil_moisture')
        - value: parsed numeric/string value

        Args:
            device_id: Tuya device ID
            since_ms: only return logs after this timestamp (milliseconds).
                      If None, uses `hours` parameter.
            hours: hours of history to fetch (default 24). Ignored if since_ms set.
            max_records: max records to fetch (default 100)
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

        # Sort chronologically (oldest first)
        parsed.sort(key=lambda x: x["timestamp_ms"])
        return parsed

    def get_device_info(self, device_id: str) -> dict:
        """Get device details from Tuya Cloud."""
        result = self._cloud.cloudrequest(f"/v1.0/devices/{device_id}")
        if not result.get("success"):
            raise RuntimeError(f"Cloud API error: {result}")
        return result.get("result", {})

    def get_gateway_subdevices(self, gateway_id: str) -> list[dict]:
        """List sub-devices connected to a Zigbee gateway."""
        result = self._cloud.cloudrequest(f"/v1.0/devices/{gateway_id}/sub-devices")
        if not result.get("success"):
            raise RuntimeError(f"Cloud API error: {result}")
        return result.get("result", [])

    def group_logs_by_timestamp(self, logs: list[dict], tolerance_ms: int = 5000) -> list[dict]:
        """Group log entries that were reported at ~the same time into readings.

        Sensors often report multiple DPs within a few seconds.
        This groups them into single reading dicts.

        Returns list of dicts with:
        - timestamp: Unix seconds
        - temperature, soil_moisture, humidity, etc. (whichever were reported)
        """
        if not logs:
            return []

        readings = []
        current_group = {"timestamp": logs[0]["timestamp"]}
        current_ts = logs[0]["timestamp_ms"]

        for log in logs:
            if abs(log["timestamp_ms"] - current_ts) > tolerance_ms:
                # New group
                readings.append(current_group)
                current_group = {"timestamp": log["timestamp"]}
                current_ts = log["timestamp_ms"]

            if "key" in log:
                current_group[log["key"]] = log["value"]
                current_group["timestamp"] = log["timestamp"]

        # Don't forget last group
        if len(current_group) > 1:  # Has at least timestamp + one value
            readings.append(current_group)

        return readings
