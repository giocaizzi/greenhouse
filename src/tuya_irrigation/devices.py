#!/usr/bin/env python3
"""Device management for Tuya irrigators and sensors."""

import os
import signal
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

        The Rainpoint IK10PW (category ggq) only supports the `switch` DP —
        no hardware countdown. Timer is implemented in software with a SIGTERM
        handler to guarantee device shutdown even on cron timeout / kill.
        """
        if minutes is None:
            # Just turn on without timer
            return self.irrigator_on(irrigator)

        success, msg = self.irrigator_on(irrigator)
        if not success:
            return False, f"Failed to start irrigation: {msg}"

        # SIGTERM guard: ensure irrigator_off() runs even if the process is
        # killed (e.g. cron job timeout). Without this, the device stays ON
        # indefinitely because there's no hardware countdown DP.
        interrupted = False

        def _sigterm_cleanup(signum, frame):
            nonlocal interrupted
            interrupted = True
            try:
                self.irrigator_off(irrigator)
            except Exception:
                pass
            raise SystemExit(128 + signum)

        prev_handler = signal.signal(signal.SIGTERM, _sigterm_cleanup)
        try:
            time.sleep(minutes * 60)
        except (SystemExit, KeyboardInterrupt):
            interrupted = True
        finally:
            signal.signal(signal.SIGTERM, prev_handler)
            try:
                self.irrigator_off(irrigator)
            except Exception:
                pass

        if interrupted:
            return True, f"Irrigation interrupted — device turned off safely (requested {minutes} min)"
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
