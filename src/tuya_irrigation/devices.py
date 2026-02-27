#!/usr/bin/env python3
"""Device management for Tuya irrigators and sensors."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tuya_irrigation.models import Irrigator, Sensor

SCRIPT_TUYA = Path(__file__).parent / "tuya_irrigation.py"


class TuyaDeviceManager:
    """Manages Tuya irrigators and sensors via existing tuya_irrigation.py script."""

    def __init__(self):
        self.client_id = os.environ.get("TUYA_CLIENT_ID", "")
        self.secret = os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = os.environ.get("TUYA_REGION", "eu")

        if not all([self.client_id, self.secret]):
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET in environment")

    def _run_tuya_script(self, device_id: str, *args: str, use_local: bool = False) -> tuple[int, str]:
        """Run tuya_irrigation.py script and return (returncode, output)."""
        env = os.environ.copy()
        env["TUYA_DEVICE_ID"] = device_id

        cmd = [sys.executable, str(SCRIPT_TUYA)]
        if use_local:
            cmd.append("local")
        cmd.extend(args)

        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode, output

    # ── Irrigator Control ─────────────────────────────────────────────────────

    def irrigator_status(self, irrigator: Irrigator) -> dict:
        """Get current status of an irrigator."""
        use_local = irrigator.type == "tuya_local"
        code, output = self._run_tuya_script(
            irrigator.tuya_device_id,
            "status" if not use_local else "local",
            "status" if use_local else "",
            use_local=False,  # Always use cloud for status unless explicitly local
        )

        if code != 0:
            return {"error": output}

        # Parse output (basic text parsing)
        status = {"raw_output": output}
        for line in output.split("\n"):
            if "State" in line:
                status["running"] = "ON" in line or "running" in line
            elif "remaining" in line.lower():
                try:
                    mins = int(line.split(":")[1].strip().split()[0])
                    status["time_remaining_minutes"] = mins
                except Exception:
                    pass
            elif "Battery" in line:
                try:
                    pct = int([x for x in line.split() if "%" in x][0].rstrip("%"))
                    status["battery_percentage"] = pct
                except Exception:
                    pass

        return status

    def irrigator_on(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator ON."""
        use_local = irrigator.type == "tuya_local"
        code, output = self._run_tuya_script(
            irrigator.tuya_device_id,
            "local" if use_local else "on",
            "on" if use_local else "",
            use_local=use_local,
        )
        return code == 0, output

    def irrigator_off(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Turn irrigator OFF."""
        use_local = irrigator.type == "tuya_local"
        code, output = self._run_tuya_script(
            irrigator.tuya_device_id,
            "local" if use_local else "off",
            "off" if use_local else "",
            use_local=use_local,
        )
        return code == 0, output

    def irrigator_start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation with optional duration."""
        use_local = irrigator.type == "tuya_local"

        if minutes is not None:
            # Start with duration (works for both local and cloud)
            args = ["start", "--minutes", str(minutes)]
            if use_local:
                args.insert(0, "local")
            
            code, output = self._run_tuya_script(
                irrigator.tuya_device_id,
                *args,
                use_local=use_local,
            )
            return code == 0, output

        # No duration — just turn on
        args = ["start"]
        if minutes:
            args.extend(["--minutes", str(minutes)])
        code, output = self._run_tuya_script(irrigator.tuya_device_id, *args)
        return code == 0, output

    def irrigator_stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Stop current irrigation."""
        code, output = self._run_tuya_script(irrigator.tuya_device_id, "stop")
        return code == 0, output

    def irrigator_set_schedule(
        self,
        irrigator: Irrigator,
        minutes: int,
        interval_hours: int,
        auto_run: bool = True,
    ) -> tuple[bool, str]:
        """Set irrigation schedule (local mode only for now)."""
        if irrigator.type != "tuya_local":
            return False, "Schedule setting only supported for local mode devices"

        code, output = self._run_tuya_script(
            irrigator.tuya_device_id,
            "local",
            "set",
            "--minutes",
            str(minutes),
            "--every-hours",
            str(interval_hours),
            "--auto-run",
            "true" if auto_run else "false",
            use_local=True,
        )
        return code == 0, output

    # ── Sensor Reading ────────────────────────────────────────────────────────

    def read_sensor(self, sensor: Sensor) -> dict:
        """Read current sensor values."""
        # For now, use the status command which works for most Tuya sensors
        code, output = self._run_tuya_script(sensor.tuya_device_id, "status")

        if code != 0:
            return {"error": output}

        # Parse sensor output (basic text parsing)
        data = {"raw_output": output}

        for line in output.split("\n"):
            line_lower = line.lower()
            if "temperature" in line_lower or "temp" in line_lower:
                try:
                    temp = float(
                        [x for x in line.split() if "°" in x or x.replace(".", "").replace("-", "").isdigit()][0]
                        .rstrip("°C")
                        .rstrip("°")
                    )
                    data["temperature"] = temp
                except Exception:
                    pass
            elif "humidity" in line_lower:
                try:
                    hum = float([x for x in line.split() if "%" in x][0].rstrip("%"))
                    data["humidity"] = hum
                except Exception:
                    pass
            elif "soil" in line_lower or "moisture" in line_lower:
                try:
                    moisture = float([x for x in line.split() if "%" in x][0].rstrip("%"))
                    data["soil_moisture"] = moisture
                except Exception:
                    pass
            elif "light" in line_lower or "lux" in line_lower:
                try:
                    light = int([x for x in line.split() if x.isdigit()][0])
                    data["light"] = light
                except Exception:
                    pass

        return data
