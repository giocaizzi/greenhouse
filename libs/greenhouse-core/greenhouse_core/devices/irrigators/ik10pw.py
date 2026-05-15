"""Rainpoint IK10PW adapter.

The IK10PW exposes a hidden Duration DP (102) over the local protocol v3.5
which the Cloud API refuses to write (error 2008). The cycle recipe is:

1. set DP 102 via local v3.5,
2. flip the cloud ``switch`` on,
3. the device runs its own countdown and auto-stops.

Both pieces are needed — cloud-only cannot bound the cycle, local-only
struggles to keep a Zigbee-gateway device awake. When local fails (IP
changed, network blip) the keep-alive fallback flips the cloud switch every
20 s to reset the device's internal 30 s auto-off, with a SIGTERM handler
that explicitly switches off so an interrupted process never leaves the
pump running.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from greenhouse_core.devices.irrigators.tuya_generic import TuyaIrrigatorAdapter
from greenhouse_core.devices.profile import IrrigatorProfile, load_profile_json
from greenhouse_core.devices.tuya_transport import TuyaTransport
from greenhouse_core.models import Irrigator

logger = logging.getLogger(__name__)

KEEP_ALIVE_INTERVAL = 20  # seconds, must be < device Duration (30s)


def alarm_indicates_no_water(value: object, bitmask: int = 0x01) -> bool:
    """True when a DP 105 reading indicates the reservoir is empty / dry pump.

    Accepts the multiple shapes a Tuya fault DP can take: an int bitmap (we
    AND against ``bitmask`` — bit 0 on IK10PW), a bool (truthy means fault),
    a string (decoded as an int when it looks numeric — Tuya local protocol
    sometimes returns DP values as strings). Returns False for None, empty
    strings, and any unrecognised type, so callers never treat "unknown" as
    "dry".

    DP 105 on the IK10PW is detected motor-current-based: when the pump
    runs dry, current drops below threshold and the bit is raised. The same
    low-current condition can be triggered by a clogged or missing filter
    on the water intake, so false positives are possible. False positives
    are safe (we stop early); the pump-protection concern is false
    negatives, which appear to be rare in practice but argue for adding a
    hardware float switch as a belt-and-suspenders independent safeguard.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value & bitmask)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            return bool(int(text) & bitmask)
        except ValueError:
            return False
    return False


def _load_profile() -> IrrigatorProfile:
    raw = load_profile_json("ik10pw.json")
    return IrrigatorProfile(
        model_key=raw["model_key"],
        vendor=raw["vendor"],
        transport=raw["transport"],
        protocol_version=raw.get("protocol_version"),
        dp_map=dict(raw.get("dp_map", {})),
        capabilities=frozenset(raw.get("capabilities", [])),
        duration_unit=raw.get("duration_unit"),
        alarm_bitmask=raw.get("alarm_bitmask"),
    )


IK10PW_PROFILE = _load_profile()


class IK10PWAdapter(TuyaIrrigatorAdapter):
    """Rainpoint IK10PW driver.

    Overrides :meth:`start` for the local-Duration-DP + cloud-switch recipe
    and :meth:`read_alarm` for the DP 105 bitmask. Status reads inherit the
    base implementation; the profile's DP map drives the field shape.
    """

    def __init__(self, transport: TuyaTransport, profile: IrrigatorProfile | None = None):
        super().__init__(profile or IK10PW_PROFILE, transport)

    # ── Cycle start ───────────────────────────────────────────────────────

    def _set_duration_local(self, irrigator: Irrigator, seconds: int) -> tuple[bool, str]:
        """Write DP 102 (Duration) over local v3.5.

        The Cloud API returns error 2008 for this custom DP, so the local
        path is the only way to control the on-device countdown.
        """
        try:
            device = self._tx.open_local(irrigator, self.profile.protocol_version)
            result = device.set_value(self.profile.dp("duration"), seconds)
            if result and result.get("Error"):
                return False, f"Local API error: {result}"
            return True, f"Duration set to {seconds}s"
        except Exception as e:
            return False, f"Local connection failed: {e}"

    def start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation with optional duration.

        Strategy:

        1. Set Duration DP (102) via local protocol to requested minutes.
        2. Send switch ON via Cloud API.
        3. Device handles its own timer — no sleep/polling needed.
        4. Device auto-stops after Duration seconds.

        If local connection fails (e.g. device IP changed), falls back to
        a keep-alive loop (sending switch ON every 20 s).
        """
        if minutes is None:
            return self.on(irrigator)

        duration_seconds = int(minutes * 60)

        # Step 1: local Duration DP
        dur_ok, dur_msg = self._set_duration_local(irrigator, duration_seconds)

        if dur_ok:
            # Step 2: cloud switch — device auto-stops after Duration seconds
            success, msg = self.on(irrigator)
            if not success:
                return False, f"Failed to start irrigation: {msg}"
            return (
                True,
                f"Irrigation started for {minutes} min (Duration DP set to {duration_seconds}s, device auto-stops)",
            )

        logger.warning("Local Duration set failed (%s), using keep-alive fallback", dur_msg)
        return self._start_keepalive(irrigator, minutes)

    def _start_keepalive(self, irrigator: Irrigator, minutes: int) -> tuple[bool, str]:
        """Sustain irrigation via keep-alive ON commands.

        Used when local protocol is unavailable (IP changed, network issue).
        Sends switch ON every 20 s to reset the device's internal auto-off
        timer. A SIGTERM handler explicitly switches off so an interrupted
        process never leaves the pump running.
        """
        success, msg = self.on(irrigator)
        if not success:
            return False, f"Failed to start irrigation: {msg}"

        total_seconds = minutes * 60
        elapsed = 0
        interrupted = False

        def _sigterm_cleanup(signum: int, frame: Any) -> None:  # noqa: ARG001
            nonlocal interrupted
            interrupted = True
            try:
                self.off(irrigator)
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
                        self.on(irrigator)
                    except Exception:
                        pass
        except (SystemExit, KeyboardInterrupt):
            interrupted = True
        finally:
            signal.signal(signal.SIGTERM, prev_handler)
            try:
                self.off(irrigator)
            except Exception:
                pass

        if interrupted:
            return (
                True,
                f"Irrigation interrupted after ~{elapsed}s — device turned off (requested {minutes} min) [keep-alive mode]",
            )
        return True, f"Irrigation completed for {minutes} min [keep-alive mode]"

    # ── Alarm read ────────────────────────────────────────────────────────

    def read_alarm(self, irrigator: Irrigator) -> dict:
        """Read the dry-run / water-shortage alarm state.

        Reads DP 105 over local protocol v3.5. On the Rainpoint IK10PW this
        flag is the only signal the firmware exposes for "the pump is
        running but no water is moving", which is the condition that
        damages the pump.

        Local-only by design — the Cloud API does not expose DP 105, and
        the cloud round-trip latency (>1 s) would defeat the purpose for a
        pump-safety check. Returns ``no_water=None`` when the device cannot
        be reached so callers can distinguish "no fault" from "no signal".
        """
        try:
            device = self._tx.open_local(irrigator, self.profile.protocol_version)
            status = device.status()
        except Exception as exc:
            return {
                "no_water": None,
                "alarm_raw": None,
                "running": None,
                "left_time": None,
                "work_status": None,
                "source": None,
                "error": f"local read failed: {exc}",
            }

        dps = (status or {}).get("dps") or {}
        alarm_raw = dps.get(str(self.profile.dp("alarm")))
        bitmask = self.profile.alarm_bitmask or 0x01
        return {
            "no_water": alarm_indicates_no_water(alarm_raw, bitmask),
            "alarm_raw": alarm_raw,
            "running": dps.get(str(self.profile.dp("switch"))),
            "left_time": dps.get(str(self.profile.dp("left_time"))),
            "work_status": dps.get(str(self.profile.dp("work_status"))),
            "source": "local",
            "error": None,
        }


__all__ = ["IK10PWAdapter", "IK10PW_PROFILE", "alarm_indicates_no_water"]
