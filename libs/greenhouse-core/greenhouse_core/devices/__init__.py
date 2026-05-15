"""Device drivers — registry, profiles, and per-model adapters.

This package replaces the legacy single-module ``greenhouse_core.devices``.
The public name :class:`TuyaDeviceManager` is preserved as a thin
compatibility shim that routes every call through :class:`DeviceRegistry`,
so existing import sites (`services/irrigation.py`, `services/sync.py`,
`app.py`, `deps.py`, `services/pump_watcher.py`, `services/bulk.py`,
plus tests) keep working unchanged through PR 1.

Backward-compat surface:

* ``from greenhouse_core.devices import TuyaDeviceManager`` — unchanged.
* ``from greenhouse_core.devices import alarm_indicates_no_water`` — unchanged.
* ``with patch("greenhouse_core.devices.tinytuya.Cloud")`` — unchanged
  (``tinytuya`` is re-exported below for the same reason the legacy module
  imported it at top level).
"""

from __future__ import annotations

import tinytuya  # re-exported for tests that patch greenhouse_core.devices.tinytuya.Cloud

from greenhouse_core.devices.irrigators.base import AbstractIrrigatorAdapter
from greenhouse_core.devices.irrigators.ik10pw import IK10PW_PROFILE, IK10PWAdapter, alarm_indicates_no_water
from greenhouse_core.devices.irrigators.tuya_generic import TuyaIrrigatorAdapter
from greenhouse_core.devices.profile import IrrigatorProfile, SensorProfile
from greenhouse_core.devices.registry import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.devices.sensors.base import AbstractSensorAdapter
from greenhouse_core.devices.sensors.tr301z import TR301Z_PROFILE, TR301ZAdapter
from greenhouse_core.devices.sensors.tuya_generic import TuyaSensorAdapter
from greenhouse_core.devices.tuya_transport import TuyaTransport
from greenhouse_core.models import Irrigator, Sensor


def build_default_registry(transport: TuyaTransport) -> DeviceRegistry:
    """Wire the canonical adapter set onto a fresh registry.

    Adapters share the same :class:`TuyaTransport` instance — that's where
    the Tuya Cloud credentials live. A per-call lambda is used as the
    factory so adapter instances stay cheap to construct.
    """
    registry = DeviceRegistry()
    registry.register_irrigator(
        IK10PW_PROFILE.model_key,
        lambda: IK10PWAdapter(transport),
    )
    # Lazy cloud import — TuyaSensorAdapter wants a TuyaCloud, not a
    # TuyaTransport. Keep them separate but co-located here.
    from greenhouse_core.cloud import TuyaCloud

    cloud = TuyaCloud(transport.client_id, transport.client_secret, transport.region)
    registry.register_sensor(
        TR301Z_PROFILE.model_key,
        lambda: TR301ZAdapter(cloud),
    )
    return registry


class TuyaDeviceManager:
    """Backwards-compatible facade over the new :class:`DeviceRegistry`.

    Existing call sites use a single mutable object that owns Cloud + DPs.
    For PR 1 we preserve that surface while routing every method through
    the registry — PR 2 will replace consumers with a registry constructor
    and drop this shim.

    The methods below mirror the old ``devices.py``'s public API one-to-one
    so the existing tests in ``tests/test_devices.py`` keep passing without
    edits.
    """

    def __init__(self) -> None:
        self._transport = TuyaTransport()
        self.client_id = self._transport.client_id
        self.secret = self._transport.client_secret
        self.region = self._transport.region
        self.cloud = self._transport.cloud
        self._registry = build_default_registry(self._transport)

    # ── Adapter resolution ────────────────────────────────────────────────

    def _irrigator(self, irrigator: Irrigator) -> AbstractIrrigatorAdapter:
        """Get the adapter for ``irrigator``. Legacy DB ``type`` values
        (``tuya_cloud``, ``tuya_local``) resolve to ``rainpoint.ik10pw``
        through the registry's alias table — the only model PR 1 ships.
        """
        return self._registry.get_irrigator(irrigator)

    # ── Public methods (delegated to adapter) ─────────────────────────────

    def _send_command(self, device_id: str, commands: dict) -> tuple[bool, str]:
        return self._transport.send_command(device_id, commands)

    def _get_local_device(self, irrigator: Irrigator):  # noqa: ANN202 - tinytuya.OutletDevice
        """Open a local tinytuya connection. Preserved for tests that patch
        this method to inject a fake device.
        """
        adapter = self._irrigator(irrigator)
        proto = adapter.profile.protocol_version
        if proto is None:
            raise ConnectionError("No local protocol declared for this model")
        return self._transport.open_local(irrigator, proto)

    def _set_duration_local(self, irrigator: Irrigator, seconds: int) -> tuple[bool, str]:
        """Write DP 102 (Duration) via local protocol.

        Routes through :meth:`_get_local_device` (not the adapter's helper)
        so tests that monkey-patch ``_get_local_device`` on the shim keep
        their canned device in the loop.
        """
        adapter = self._irrigator(irrigator)
        if not isinstance(adapter, IK10PWAdapter):
            return False, "Model has no Duration DP"
        try:
            device = self._get_local_device(irrigator)
            result = device.set_value(adapter.profile.dp("duration"), seconds)
            if result and result.get("Error"):
                return False, f"Local API error: {result}"
            return True, f"Duration set to {seconds}s"
        except Exception as e:
            return False, f"Local connection failed: {e}"

    def irrigator_status(self, irrigator: Irrigator) -> dict:
        """Get current status. Tries local via :meth:`_get_local_device`
        (overridable in tests), falls back to cloud ``getstatus``.
        """
        adapter = self._irrigator(irrigator)
        proto = adapter.profile.protocol_version
        if proto is not None and adapter.profile.has_capability("supports_local_status"):
            try:
                device = self._get_local_device(irrigator)
                live = device.status()
                if live and "dps" in live:
                    dps = live["dps"]
                    out: dict = {"source": "local"}
                    for key in ("switch", "duration", "left_time", "work_status", "auto_run"):
                        if key in adapter.profile.dp_map:
                            out_key = "running" if key == "switch" else key
                            out[out_key] = dps.get(str(adapter.profile.dp(key)))
                    return out
            except Exception:
                pass

        result = self._transport.get_status(irrigator.tuya_device_id)
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

    def irrigator_on(self, irrigator: Irrigator) -> tuple[bool, str]:
        adapter = self._irrigator(irrigator)
        # Both IK10PW and TuyaIrrigatorAdapter expose .on; the ABC does not
        # require it, but every concrete Tuya adapter inherits the helper.
        return adapter.on(irrigator)  # type: ignore[attr-defined]

    def irrigator_off(self, irrigator: Irrigator) -> tuple[bool, str]:
        adapter = self._irrigator(irrigator)
        return adapter.off(irrigator)  # type: ignore[attr-defined]

    def irrigator_start(self, irrigator: Irrigator, minutes: int | None = None) -> tuple[bool, str]:
        """Start irrigation. Walks the IK10PW recipe (local Duration DP →
        cloud switch → keep-alive fallback) via the shim's own methods so
        tests can patch ``_set_duration_local`` / ``_irrigator_start_keepalive``
        independently.
        """
        import logging as _logging

        if minutes is None or minutes == 0:
            return self.irrigator_on(irrigator)

        adapter = self._irrigator(irrigator)
        if not isinstance(adapter, IK10PWAdapter):
            return adapter.start(irrigator, minutes)

        duration_seconds = int(minutes * 60)
        dur_ok, dur_msg = self._set_duration_local(irrigator, duration_seconds)
        if dur_ok:
            success, msg = self.irrigator_on(irrigator)
            if not success:
                return False, f"Failed to start irrigation: {msg}"
            return (
                True,
                f"Irrigation started for {minutes} min (Duration DP set to {duration_seconds}s, device auto-stops)",
            )
        _logging.getLogger(__name__).warning("Local Duration set failed (%s), using keep-alive fallback", dur_msg)
        return self._irrigator_start_keepalive(irrigator, minutes)

    def _irrigator_start_keepalive(self, irrigator: Irrigator, minutes: int) -> tuple[bool, str]:
        """Keep-alive fallback. Delegates to the IK10PW adapter; tests may
        patch this method to short-circuit the loop.
        """
        adapter = self._irrigator(irrigator)
        if not isinstance(adapter, IK10PWAdapter):
            return False, "Model has no keep-alive fallback"
        return adapter._start_keepalive(irrigator, minutes)

    def irrigator_stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        return self._irrigator(irrigator).stop(irrigator)

    def read_irrigator_alarm(self, irrigator: Irrigator) -> dict:
        """Read DP 105 (no-water bit).

        Routes through :meth:`_get_local_device` so tests that monkey-patch
        the local-device factory drive the read. Same dict shape as the
        adapter contract.
        """
        adapter = self._irrigator(irrigator)
        if not isinstance(adapter, IK10PWAdapter):
            # No alarm DP on this model.
            return adapter.read_alarm(irrigator)
        try:
            device = self._get_local_device(irrigator)
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
        alarm_raw = dps.get(str(adapter.profile.dp("alarm")))
        bitmask = adapter.profile.alarm_bitmask or 0x01
        return {
            "no_water": alarm_indicates_no_water(alarm_raw, bitmask),
            "alarm_raw": alarm_raw,
            "running": dps.get(str(adapter.profile.dp("switch"))),
            "left_time": dps.get(str(adapter.profile.dp("left_time"))),
            "work_status": dps.get(str(adapter.profile.dp("work_status"))),
            "source": "local",
            "error": None,
        }

    def read_sensor(self, sensor: Sensor) -> dict:
        adapter = self._registry.get_sensor(sensor)
        if adapter is None:
            return {"error": f"No adapter for sensor type {sensor.type!r}"}
        return adapter.read_live(sensor)


__all__ = [
    "AbstractIrrigatorAdapter",
    "AbstractSensorAdapter",
    "DeviceRegistry",
    "IK10PWAdapter",
    "IK10PW_PROFILE",
    "IrrigatorProfile",
    "SensorProfile",
    "TR301ZAdapter",
    "TR301Z_PROFILE",
    "TuyaDeviceManager",
    "TuyaIrrigatorAdapter",
    "TuyaSensorAdapter",
    "TuyaTransport",
    "UnknownDeviceModel",
    "alarm_indicates_no_water",
    "build_default_registry",
    "tinytuya",
]
