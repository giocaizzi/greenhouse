#!/usr/bin/env python3
"""Device management for Tuya irrigators and sensors."""

import os

import tinytuya

from tuya_irrigation.models import Irrigator, Sensor


class TuyaDeviceManager:
    """Manages Tuya irrigators and sensors via Tuya Cloud API."""

    def __init__(self):
        self.client_id = os.environ.get("TUYA_CLIENT_ID", "")
        self.secret = os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = os.environ.get("TUYA_REGION", "eu")

        if not all([self.client_id, self.secret]):
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET in environment")

        self.cloud = tinytuya.Cloud(
            apiRegion=self.region,
            apiKey=self.client_id,
            apiSecret=self.secret,
        )

    # ── Irrigator Control ─────────────────────────────────────────────────────

    def irrigator_status(self, irrigator: Irrigator) -> dict:
        """Get current status of an irrigator."""
        result = self.cloud.getstatus(irrigator.tuya_device_id)

        if not result.get("success"):
            return {"error": f"Cloud API error: {result}"}

        # Parse result
        status = {}
        for item in result.get("result", []):
            code = item.get("code")
            value = item.get("value")
            if code == "switch":
                status["running"] = value
            elif code == "work_state":
                status["work_state"] = value

        return status

    def irrigator_on(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator ON."""
        commands = {"commands": [{"code": "switch", "value": True}]}
        result = self.cloud.sendcommand(irrigator.tuya_device_id, commands)

        if result.get("success"):
            return True, "Device turned ON"
        else:
            return False, f"Cloud API error: {result}"

    def irrigator_off(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator OFF."""
        commands = {"commands": [{"code": "switch", "value": False}]}
        result = self.cloud.sendcommand(irrigator.tuya_device_id, commands)

        if result.get("success"):
            return True, "Device turned OFF"
        else:
            return False, f"Cloud API error: {result}"

    def irrigator_start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation with optional duration.

        For Tuya irrigators, this typically means:
        1. Turn on the switch
        2. Set timer duration (if device supports it)

        Returns (success, message).
        """
        if minutes is None:
            # Just turn on without timer
            return self.irrigator_on(irrigator)

        # Turn on + set timer
        # Most Tuya irrigators use these codes:
        # - "switch": bool (on/off)
        # - "timer_1" or "countdown_1": int (seconds remaining)
        commands = {
            "commands": [
                {"code": "switch", "value": True},
                {"code": "countdown_1", "value": minutes * 60},  # seconds
            ]
        }

        result = self.cloud.sendcommand(irrigator.tuya_device_id, commands)

        if result.get("success"):
            return True, f"Irrigation started for {minutes} minutes"
        else:
            return False, f"Cloud API error: {result}"

    def irrigator_stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Stop current irrigation."""
        return self.irrigator_off(irrigator)

    def irrigator_set_schedule(
        self,
        irrigator: Irrigator,
        minutes: int,
        interval_hours: int,
        auto_run: bool = True,
    ) -> tuple[bool, str]:
        """Set irrigation schedule.

        Note: Schedule management via Cloud API is device-specific and may not
        be supported by all irrigators. This is a placeholder for future implementation.
        """
        return False, "Schedule setting via Cloud API not yet implemented"

    # ── Sensor Reading ────────────────────────────────────────────────────────

    def read_sensor(self, sensor: Sensor) -> dict:
        """Read current sensor values via Tuya Cloud API.

        Uses cloud.py for centralized datapoint parsing.
        """
        try:
            from tuya_irrigation.cloud import TuyaCloud

            cloud = TuyaCloud(self.client_id, self.secret, self.region)
            return cloud.get_live_reading(sensor.tuya_device_id)
        except Exception as e:
            return {"error": str(e)}
