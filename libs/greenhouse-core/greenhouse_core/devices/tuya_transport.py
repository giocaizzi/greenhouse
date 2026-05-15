"""Shared Tuya Cloud + local connection helpers used by Tuya-based adapters.

Centralises credential loading and the ``tinytuya.Cloud`` / ``OutletDevice``
constructors so per-model adapters don't each re-do the work the old
``TuyaDeviceManager`` did inline. Adapters receive a :class:`TuyaTransport`
in their constructor.
"""

from __future__ import annotations

import json as _json
import os

import tinytuya

from greenhouse_core.models import Irrigator

LOCAL_TIMEOUT = 5


class TuyaTransport:
    """Thin wrapper over ``tinytuya.Cloud`` plus a local-device factory.

    Adapters that talk to Tuya share one transport instance; the transport
    caches the cloud client. ``open_local`` is parametrised on the profile's
    protocol version, so a future model that uses local v3.3 only has to
    declare ``protocol_version: 3.3`` in its profile JSON — no transport
    change required.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        region: str | None = None,
    ):
        self.client_id = client_id or os.environ.get("TUYA_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TUYA_CLIENT_SECRET", "")
        self.region = region or os.environ.get("TUYA_REGION", "eu")

        if not all([self.client_id, self.client_secret]):
            raise ValueError("Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET in environment")

        self.cloud = tinytuya.Cloud(
            apiRegion=self.region,
            apiKey=self.client_id,
            apiSecret=self.client_secret,
        )

    def send_command(self, device_id: str, commands: dict) -> tuple[bool, str]:
        """Send a Cloud-API command payload (already shaped as ``{"commands": [...]}``)."""
        result = self.cloud.sendcommand(device_id, commands)
        if result.get("success"):
            return True, "Command succeeded"
        return False, f"Cloud API error: {result}"

    def get_status(self, device_id: str) -> dict:
        """Return the raw Cloud-API ``getstatus`` response."""
        return self.cloud.getstatus(device_id)

    def open_local(self, irrigator: Irrigator, protocol_version: float) -> tinytuya.OutletDevice:
        """Build a connected ``OutletDevice`` for ``irrigator``.

        Looks up ``local_key`` via the Cloud API device list and ``local_ip``
        from the irrigator's ``config`` blob (JSON string or dict). Raises
        ``ConnectionError`` when either is missing.
        """
        devices = self.cloud.getdevices()
        local_key: str | None = None
        if isinstance(devices, list):
            for d in devices:
                if d.get("id") == irrigator.tuya_device_id:
                    local_key = d.get("key")
                    break

        if not local_key:
            raise ConnectionError(f"Could not find local key for device {irrigator.tuya_device_id}")

        config = irrigator.config or {}
        if isinstance(config, str):
            try:
                config = _json.loads(config)
            except (ValueError, TypeError):
                config = {}
        local_ip = config.get("device_ip")
        if not local_ip:
            raise ConnectionError(f"No device_ip in config for irrigator {irrigator.tuya_device_id}.")

        device = tinytuya.OutletDevice(irrigator.tuya_device_id, local_ip, local_key)
        device.set_version(protocol_version)
        device.set_socketTimeout(LOCAL_TIMEOUT)
        return device
