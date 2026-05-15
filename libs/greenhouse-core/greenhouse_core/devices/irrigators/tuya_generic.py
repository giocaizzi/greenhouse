"""Generic Tuya irrigator adapter.

Profile-driven default behaviour: cloud switch on/off, status reads via
local then cloud, no alarm DP. Model-specific subclasses override ``start``
or ``read_health`` for firmware-specific recipes.
"""

from __future__ import annotations

import time

from greenhouse_core.devices.health import DeviceHealthState
from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.profile import IrrigatorProfile
from greenhouse_core.devices.tuya_transport import TuyaTransport
from greenhouse_core.models import Irrigator


class TuyaIrrigatorAdapter(AbstractIrrigatorAdapter):
    """Default Tuya driver. Switch on/off via Cloud API.

    Models that need anything beyond plain on/off (e.g. a local Duration DP,
    a keep-alive fallback, or an alarm read) subclass this adapter and
    override the relevant method while reusing the cloud helpers.
    """

    def __init__(self, profile: IrrigatorProfile, transport: TuyaTransport):
        self.profile = profile
        self._tx = transport

    # ── Helpers ───────────────────────────────────────────────────────────

    def _send_switch(self, irrigator: Irrigator, value: bool) -> tuple[bool, str]:
        commands = {"commands": [{"code": "switch", "value": value}]}
        success, _ = self._tx.send_command(irrigator.tuya_device_id, commands)
        word = "ON" if value else "OFF"
        return success, f"Device turned {word}" if success else f"Failed to turn {word} device"

    # ── Public API ────────────────────────────────────────────────────────

    def on(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Switch the irrigator on via Cloud API."""
        return self._send_switch(irrigator, True)

    def off(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Switch the irrigator off via Cloud API."""
        return self._send_switch(irrigator, False)

    def start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Switch on. Subclasses override to handle a Duration DP recipe."""
        return self.on(irrigator)

    def stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Switch off."""
        return self.off(irrigator)

    def status(self, irrigator: Irrigator) -> dict:
        """Return current status. Tries local first, falls back to cloud.

        Local read fields (``running``, ``duration``, ``left_time``,
        ``work_status``, ``auto_run``, ``source="local"``) are populated from
        the profile's DP map; missing DPs surface as ``None``.
        """
        proto = self.profile.protocol_version
        if proto is not None and self.profile.has_capability("supports_local_status"):
            try:
                device = self._tx.open_local(irrigator, proto)
                live = device.status()
                if live and "dps" in live:
                    dps = live["dps"]
                    out: dict = {"source": "local"}
                    for key in ("switch", "duration", "left_time", "work_status", "auto_run"):
                        if key in self.profile.dp_map:
                            out[self._status_key(key)] = dps.get(str(self.profile.dp(key)))
                    return out
            except Exception:
                pass  # fall back to cloud

        result = self._tx.get_status(irrigator.tuya_device_id)
        if not result.get("success"):
            return {"error": f"Cloud API error: {result}"}

        status: dict = {"source": "cloud"}
        for item in result.get("result", []):
            code = item.get("code")
            value = item.get("value")
            if code == "switch":
                status["running"] = value
            elif code == "work_state":
                status["work_state"] = value
        return status

    @staticmethod
    def _status_key(profile_key: str) -> str:
        # ``switch`` is exposed publicly as ``running`` to match the legacy
        # status dict shape consumers expect.
        return "running" if profile_key == "switch" else profile_key

    def read_health(self, irrigator: Irrigator) -> DeviceHealthState:
        """Default: no health surface — return a clean snapshot.

        Subclasses with a water-shortage DP, battery, or signal report
        override this to do a real read. Generic Tuya cloud-only models
        have no health surface beyond reachability, which the monitor
        derives from the broader sync cycle.
        """
        return DeviceHealthState(
            observed_at=int(time.time()),
            alarms=frozenset(),
        )
