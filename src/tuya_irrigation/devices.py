#!/usr/bin/env python3
"""Device management for Tuya irrigators and sensors."""

import os
import time

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

    def _send_command(self, device_id: str, commands: dict) -> tuple[bool, str]:
        """Send command to device via Cloud API.

        Args:
            device_id: Tuya device ID
            commands: Command dict in format {"commands": [{"code": str, "value": any}, ...]}

        Returns:
            (success, message) tuple
        """
        result = self.cloud.sendcommand(device_id, commands)
        if result.get("success"):
            return True, "Command succeeded"
        else:
            return False, f"Cloud API error: {result}"

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
        success, _ = self._send_command(irrigator.tuya_device_id, commands)
        return success, "Device turned ON" if success else "Failed to turn ON device"

    def irrigator_off(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator OFF."""
        commands = {"commands": [{"code": "switch", "value": False}]}
        success, _ = self._send_command(irrigator.tuya_device_id, commands)
        return success, "Device turned OFF" if success else "Failed to turn OFF device"

    def irrigator_start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation with optional duration.

        Args:
            irrigator: Irrigator instance
            minutes: Duration in minutes (None = just turn on)

        Returns:
            (success, message) tuple
        """
        if minutes is None:
            # Just turn on without timer
            return self.irrigator_on(irrigator)

        # This device (Rainpoint IK10PW, category ggq) only supports the `switch` DP.
        # countdown_1 is NOT available — we implement the timer ourselves.
        success, msg = self.irrigator_on(irrigator)
        if not success:
            return False, f"Failed to start irrigation: {msg}"

        # Wait for the requested duration, then stop
        time.sleep(minutes * 60)
        self.irrigator_off(irrigator)

        return True, f"Irrigation completed for {minutes} minutes"

    def irrigator_stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Stop current irrigation."""
        return self.irrigator_off(irrigator)

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
