#!/usr/bin/env python3
"""Device management for Tuya irrigators and sensors.

Hybrid approach for Rainpoint IK10PW:
- Cloud API for switch on/off (reliable, works through Zigbee gateway)
- Local API (v3.5) for custom DPs like Duration (dp_id 102) that the
  Cloud API won't let us write (error 2008)

The device has a hidden Duration DP (102) that controls how long it
irrigates per switch-on cycle. Default is 30s. We set it to the
requested duration via local protocol before activating the switch,
so the device handles its own timer — no sleep loops needed.
"""

import os
import sys
import time

import tinytuya

from tuya_irrigation.models import Irrigator, Sensor

# DP IDs for Rainpoint IK10PW (category ggq, protocol v3.5)
DP_SWITCH = 1  # bool: on/off
DP_DURATION = 102  # int: irrigation duration in seconds
DP_INTERVAL = 103  # int: auto-irrigation interval in hours
DP_LEFTTIME = 104  # int: remaining seconds (read-only)
DP_ALARM = 105  # bitmap: alarm flags
DP_WORKSTATUS = 106  # enum: work status
DP_NEXT = 107  # int: next irrigation timestamp
DP_POWERSTATUS = 108  # enum: power status
DP_AUTORUN = 109  # bool: auto-irrigation enabled

# Default local IP — set via TUYA_DEVICE_IP env var or irrigator config
DEFAULT_LOCAL_IP = os.environ.get("TUYA_DEVICE_IP")
LOCAL_PROTOCOL = 3.5
LOCAL_TIMEOUT = 5


class TuyaDeviceManager:
    """Manages Tuya irrigators and sensors via Cloud + Local API (hybrid)."""

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

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send_command(self, device_id: str, commands: dict) -> tuple[bool, str]:
        """Send command to device via Cloud API."""
        result = self.cloud.sendcommand(device_id, commands)
        if result.get("success"):
            return True, "Command succeeded"
        else:
            return False, f"Cloud API error: {result}"

    def _get_local_device(self, irrigator: Irrigator) -> tinytuya.OutletDevice:
        """Create a local tinytuya connection to the irrigator.

        Uses local_key from Cloud API device list and local_ip from
        irrigator config (fallback to DEFAULT_LOCAL_IP).
        """
        import json as _json

        # Get local key from cloud
        devices = self.cloud.getdevices()
        local_key = None
        if isinstance(devices, list):
            for d in devices:
                if d.get("id") == irrigator.tuya_device_id:
                    local_key = d.get("key")
                    break

        if not local_key:
            raise ConnectionError(f"Could not find local key for device {irrigator.tuya_device_id}")

        # Config may be a JSON string or a dict
        config = irrigator.config or {}
        if isinstance(config, str):
            try:
                config = _json.loads(config)
            except (ValueError, TypeError):
                config = {}
        local_ip = config.get("device_ip") or DEFAULT_LOCAL_IP
        if not local_ip:
            raise ConnectionError(
                f"No local IP for device {irrigator.tuya_device_id}. "
                "Set TUYA_DEVICE_IP env var or device_ip in irrigator config."
            )

        device = tinytuya.OutletDevice(irrigator.tuya_device_id, local_ip, local_key)
        device.set_version(LOCAL_PROTOCOL)
        device.set_socketTimeout(LOCAL_TIMEOUT)
        return device

    def _set_duration_local(self, irrigator: Irrigator, seconds: int) -> tuple[bool, str]:
        """Set the Duration DP (102) via local protocol.

        Cloud API returns error 2008 for this custom DP, so we must
        use local protocol v3.5 which has full DP access.
        """
        try:
            device = self._get_local_device(irrigator)
            result = device.set_value(DP_DURATION, seconds)
            if result and result.get("Error"):
                return False, f"Local API error: {result}"
            return True, f"Duration set to {seconds}s"
        except Exception as e:
            return False, f"Local connection failed: {e}"

    # ── Irrigator Control ─────────────────────────────────────────────────────

    def irrigator_status(self, irrigator: Irrigator) -> dict:
        """Get current status of an irrigator.

        Tries local first (full DP access), falls back to cloud.
        """
        try:
            device = self._get_local_device(irrigator)
            status = device.status()
            if status and "dps" in status:
                dps = status["dps"]
                return {
                    "running": dps.get(str(DP_SWITCH), False),
                    "duration": dps.get(str(DP_DURATION)),
                    "left_time": dps.get(str(DP_LEFTTIME)),
                    "work_status": dps.get(str(DP_WORKSTATUS)),
                    "auto_run": dps.get(str(DP_AUTORUN)),
                    "source": "local",
                }
        except Exception:
            pass  # Fall back to cloud

        result = self.cloud.getstatus(irrigator.tuya_device_id)
        if not result.get("success"):
            return {"error": f"Cloud API error: {result}"}

        status = {"source": "cloud"}
        for item in result.get("result", []):
            code = item.get("code")
            value = item.get("value")
            if code == "switch":
                status["running"] = value
            elif code == "work_state":
                status["work_state"] = value
        return status

    def irrigator_on(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator ON via Cloud API."""
        commands = {"commands": [{"code": "switch", "value": True}]}
        success, _ = self._send_command(irrigator.tuya_device_id, commands)
        return success, "Device turned ON" if success else "Failed to turn ON device"

    def irrigator_off(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator OFF via Cloud API."""
        commands = {"commands": [{"code": "switch", "value": False}]}
        success, _ = self._send_command(irrigator.tuya_device_id, commands)
        return success, "Device turned OFF" if success else "Failed to turn OFF device"

    def irrigator_start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation with optional duration.

        Args:
            irrigator: Irrigator instance
            minutes: Duration in minutes (None = just turn on with current Duration DP)

        Returns:
            (success, message) tuple

        Strategy:
        1. Set Duration DP (102) via local protocol to requested minutes
        2. Send switch ON via Cloud API
        3. Device handles its own timer — no sleep/polling needed
        4. Device auto-stops after Duration seconds

        If local connection fails (e.g. device IP changed), falls back to
        keep-alive loop (sending switch ON every 20s).
        """
        if minutes is None:
            return self.irrigator_on(irrigator)

        duration_seconds = int(minutes * 60)

        # Step 1: Set Duration DP via local protocol
        dur_ok, dur_msg = self._set_duration_local(irrigator, duration_seconds)

        if dur_ok:
            # Step 2: Activate switch — device will auto-stop after duration
            success, msg = self.irrigator_on(irrigator)
            if not success:
                return False, f"Failed to start irrigation: {msg}"
            return (
                True,
                f"Irrigation started for {minutes} min (Duration DP set to {duration_seconds}s, device auto-stops)",
            )
        else:
            # Fallback: keep-alive loop if local connection fails
            print(f"⚠️  Local Duration set failed ({dur_msg}), using keep-alive fallback", file=sys.stderr)
            return self._irrigator_start_keepalive(irrigator, minutes)

    def _irrigator_start_keepalive(self, irrigator: Irrigator, minutes: int) -> tuple[bool, str]:
        """Fallback: sustain irrigation via keep-alive ON commands.

        Used when local protocol is unavailable (IP changed, network issue).
        Sends switch ON every 20s to reset the device's internal auto-off timer.
        """
        import signal

        KEEP_ALIVE_INTERVAL = 20  # seconds, must be < device Duration (30s)

        success, msg = self.irrigator_on(irrigator)
        if not success:
            return False, f"Failed to start irrigation: {msg}"

        total_seconds = minutes * 60
        elapsed = 0
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
            while elapsed < total_seconds and not interrupted:
                sleep_for = min(KEEP_ALIVE_INTERVAL, total_seconds - elapsed)
                time.sleep(sleep_for)
                elapsed += sleep_for
                if elapsed < total_seconds and not interrupted:
                    try:
                        self.irrigator_on(irrigator)
                    except Exception:
                        pass
        except (SystemExit, KeyboardInterrupt):
            interrupted = True
        finally:
            signal.signal(signal.SIGTERM, prev_handler)
            try:
                self.irrigator_off(irrigator)
            except Exception:
                pass

        if interrupted:
            return (
                True,
                f"Irrigation interrupted after ~{elapsed}s — device turned off (requested {minutes} min) [keep-alive mode]",
            )
        return True, f"Irrigation completed for {minutes} min [keep-alive mode]"

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
