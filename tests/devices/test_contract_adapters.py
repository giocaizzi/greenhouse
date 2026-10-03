"""Characterization: device profiles, the DeviceGateway and every adapter, call by call.

Pins *what the hardware-facing code does today* so a behavior-preserving refactor
can prove it sends exactly the same Tuya traffic. Everything runs against a
recording double:

- ``RecordingCloud`` stands in for the one ``tinytuya.Cloud`` (injected through
  ``DeviceGateway(raw=...)`` — no credentials from the environment, no network);
- ``greenhouse_core.devices.tinytuya.OutletDevice`` (the documented patch target)
  is replaced by a recording local-device class.

Both append to one shared ``log`` so the *interleaving* of local and Cloud calls
(e.g. "DP 102 over local v3.5, then the Cloud switch") is part of the contract.

Invariants pinned here: #1 (IK10PW speaks local protocol 3.5) and the device half
of #8 (``open_local`` resolves ``local_key`` from config/cache with zero Cloud
calls; ``get_live_reading`` is single-call — v1.0 ``getstatus`` only when the
v2.0 shadow *fails*; the on/off switch pulse goes through the Cloud API and the
keep-alive fallback re-pulses it via the Cloud). Bug B-2 (keep-alive SIGTERM
handler off the main thread) is pinned as current behavior, not fixed.
"""

from __future__ import annotations

import dataclasses
import logging
import signal
import threading
import time
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_DEVICE_ID, FAKE_DEVICE_IP, FAKE_LOCAL_KEY, FAKE_SENSOR_ID
from golden import FROZEN_TS, assert_golden_json
from greenhouse_core.devices import (
    DATAPOINT_PARSERS,
    IK10PW_PROFILE,
    TR301Z_PROFILE,
    DeviceGateway,
    DeviceHealthState,
    DeviceRegistry,
    HealthAlarm,
    IK10PWAdapter,
    IrrigatorProfile,
    TR301ZAdapter,
    TuyaIrrigatorAdapter,
    TuyaSensorAdapter,
    UnknownDeviceModel,
    alarm_indicates_no_water,
    build_default_registry,
)
from greenhouse_core.devices import gateway as gateway_module
from greenhouse_core.devices.irrigators import ik10pw as ik10pw_module
from greenhouse_core.devices.profile import load_profile_json
from greenhouse_core.models import Irrigator, Sensor, SensorReading

GOLDEN = "ingress_devices"

SWITCH_ON = {"commands": [{"code": "switch", "value": True}]}
SWITCH_OFF = {"commands": [{"code": "switch", "value": False}]}
SHADOW_PATH = f"/v2.0/cloud/thing/{FAKE_SENSOR_ID}/shadow/properties"


# ── Recording doubles ──────────────────────────────────────────────────────────


class Seq:
    """A scripted sequence of responses for consecutive calls to one Cloud method."""

    def __init__(self, *items: object) -> None:
        self.items = list(items)


class RecordingCloud:
    """Stand-in for ``tinytuya.Cloud``: records every call, replays canned responses.

    A response may be a value, an exception instance (raised), or a :class:`Seq`
    (popped in order; the last entry repeats). Unconfigured methods raise so an unexpected
    Cloud call can never pass silently.
    """

    def __init__(self, log: list) -> None:
        self.log = log
        self.responses: dict[str, object] = {}

    def set(self, method: str, response: object) -> None:
        self.responses[method] = response

    def _respond(self, method: str):
        if method not in self.responses:
            raise AssertionError(f"unexpected Cloud call: {method}")
        resp = self.responses[method]
        if isinstance(resp, Seq):
            resp = resp.items.pop(0) if len(resp.items) > 1 else resp.items[0]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def cloudrequest(self, url):
        self.log.append(("cloud.cloudrequest", url))
        return self._respond("cloudrequest")

    def getstatus(self, device_id):
        self.log.append(("cloud.getstatus", device_id))
        return self._respond("getstatus")

    def getdevicelog(self, device_id, **kwargs):
        self.log.append(("cloud.getdevicelog", device_id, kwargs))
        return self._respond("getdevicelog")

    def sendcommand(self, device_id, commands):
        self.log.append(("cloud.sendcommand", device_id, commands))
        return self._respond("sendcommand")

    def getdevices(self):
        self.log.append(("cloud.getdevices",))
        return self._respond("getdevices")


class Harness:
    """One recording Cloud + recording local-device class sharing a call log."""

    def __init__(self, monkeypatch) -> None:
        self.log: list = []
        self.cloud = RecordingCloud(self.log)
        self.local_status: object = {"dps": {}}
        self.local_set_value: object = None
        self.local_status_error: BaseException | None = None
        harness = self

        class RecordingOutletDevice:
            def __init__(self, dev_id, address, local_key):
                harness.log.append(("local.OutletDevice", dev_id, address, local_key))

            def set_version(self, version):
                harness.log.append(("local.set_version", version))

            def set_socketTimeout(self, seconds):  # noqa: N802 — tinytuya's name
                harness.log.append(("local.set_socketTimeout", seconds))

            def set_value(self, dp, value):
                harness.log.append(("local.set_value", dp, value))
                return harness.local_set_value

            def status(self):
                harness.log.append(("local.status",))
                if harness.local_status_error is not None:
                    raise harness.local_status_error
                return harness.local_status

        monkeypatch.setattr("greenhouse_core.devices.tinytuya.OutletDevice", RecordingOutletDevice)
        self.gateway = DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "eu", raw=self.cloud)

    def cloud_calls(self) -> list:
        return [c for c in self.log if c[0].startswith("cloud.")]


@pytest.fixture
def harness(monkeypatch, frozen_clock) -> Harness:
    return Harness(monkeypatch)


def _irrigator(config: dict | str | None = None, *, device_type: str = "rainpoint.ik10pw") -> Irrigator:
    return Irrigator(
        id=7,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="Contract Pump",
        type=device_type,
        config={"device_ip": FAKE_DEVICE_IP, "local_key": FAKE_LOCAL_KEY} if config is None else config,
    )


def _sensor(device_type: str = "tuya.tr301z") -> Sensor:
    return Sensor(
        id=9,
        cluster_id=1,
        tuya_device_id=FAKE_SENSOR_ID,
        name="Contract Probe",
        type=device_type,
        plant_id=None,
        config={},
    )


def _reading(**fields) -> SensorReading:
    base = {"sensor_id": 9, "timestamp": FROZEN_TS - 600}
    base.update(fields)
    return SensorReading(**base)


def _state_dict(state: DeviceHealthState) -> dict:
    out = dataclasses.asdict(state)
    out["alarms"] = sorted(a.value for a in state.alarms)  # frozenset → stable order
    return out


def _local_open_calls(key: str = FAKE_LOCAL_KEY, ip: str = FAKE_DEVICE_IP) -> list:
    return [
        ("local.OutletDevice", FAKE_DEVICE_ID, ip, key),
        ("local.set_version", 3.5),
        ("local.set_socketTimeout", 5),
    ]


# ── Profiles, parsers, registry (data contracts) ───────────────────────────────


def _profile_dict(profile) -> dict:
    out = {}
    for f in dataclasses.fields(profile):
        value = getattr(profile, f.name)
        if isinstance(value, frozenset):
            value = sorted(value)
        elif f.name == "dp_parsers":
            value = list(value)  # insertion order comes from the profile JSON
        out[f.name] = value
    return out


def _parser_matrix() -> dict:
    samples = [0, 1, 251, -15, 12.7, "220", "12.7", "abc", "false", "", None, True]
    matrix = {}
    for code, parser in DATAPOINT_PARSERS.items():
        row = []
        for raw in samples:
            try:
                key, value = parser(raw)
                row.append({"raw": raw, "key": key, "value": value, "type": type(value).__name__})
            except Exception as exc:  # noqa: BLE001 — the exception type IS the contract
                row.append({"raw": raw, "raises": type(exc).__name__})
        matrix[code] = row
    return matrix


def test_profiles_parsers_and_registry_golden():
    """Profile JSON → typed profiles, the DP parser table, and the registry wiring."""
    registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
    assert_golden_json(
        f"{GOLDEN}/profiles_parsers_registry.json",
        {
            "profile_json": {
                "ik10pw.json": load_profile_json("ik10pw.json"),
                "tr301z.json": load_profile_json("tr301z.json"),
            },
            "typed_profiles": {
                "IK10PW_PROFILE": _profile_dict(IK10PW_PROFILE),
                "TR301Z_PROFILE": _profile_dict(TR301Z_PROFILE),
            },
            "datapoint_parser_codes_in_order": list(DATAPOINT_PARSERS),
            "datapoint_parsers": _parser_matrix(),
            "registry": {
                "irrigator_keys": list(registry.registered_irrigator_keys()),
                "sensor_keys": list(registry.registered_sensor_keys()),
            },
            "health_capabilities": {
                "IK10PWAdapter": sorted(a.value for a in IK10PWAdapter.health_capabilities),
                "TR301ZAdapter": sorted(a.value for a in TR301ZAdapter.health_capabilities),
                "TuyaIrrigatorAdapter": sorted(a.value for a in TuyaIrrigatorAdapter.health_capabilities),
                "TuyaSensorAdapter": sorted(a.value for a in TuyaSensorAdapter.health_capabilities),
            },
            "constants": {
                "KEEP_ALIVE_INTERVAL": ik10pw_module.KEEP_ALIVE_INTERVAL,
                "LOCAL_TIMEOUT": gateway_module.LOCAL_TIMEOUT,
            },
        },
    )


def test_invariant_1_ik10pw_profile_is_protocol_3_5():
    """Invariant #1: Rainpoint IK10PW speaks local protocol v3.5 (not 3.3)."""
    assert load_profile_json("ik10pw.json")["protocol_version"] == 3.5
    assert IK10PW_PROFILE.protocol_version == 3.5
    assert IK10PWAdapter(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object())).profile is IK10PW_PROFILE
    assert IK10PW_PROFILE.dp("switch") == 1
    assert IK10PW_PROFILE.dp("duration") == 102
    assert IK10PW_PROFILE.dp("alarm") == 105
    assert IK10PW_PROFILE.alarm_bitmask == 0x01


def test_profile_dp_unknown_name_raises_keyerror():
    with pytest.raises(KeyError):
        IK10PW_PROFILE.dp("rain_sensor")
    assert IK10PW_PROFILE.has_capability("supports_local_status") is True
    assert IK10PW_PROFILE.has_capability("nope") is False
    assert TR301Z_PROFILE.has_capability("reports_battery") is True


class TestRegistryResolution:
    def test_factories_build_a_fresh_adapter_per_lookup_sharing_one_gateway(self):
        gateway = DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object())
        registry = build_default_registry(gateway)
        a = registry.get_irrigator(_irrigator())
        b = registry.get_irrigator(_irrigator())
        s = registry.get_sensor(_sensor())
        assert type(a) is IK10PWAdapter and type(s) is TR301ZAdapter
        assert a is not b
        assert a._gateway is gateway and b._gateway is gateway and s._gateway is gateway

    def test_only_the_model_key_resolves_to_ik10pw(self):
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        assert type(registry.get_irrigator(_irrigator(device_type="rainpoint.ik10pw"))) is IK10PWAdapter

    @pytest.mark.parametrize("raw_type", [None, "", "tuya_cloud", "tuya_local"])
    def test_legacy_and_empty_irrigator_types_are_unknown(self, raw_type):
        """OD3: the legacy alias table is gone — these values are unknown models."""
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        with pytest.raises(UnknownDeviceModel) as excinfo:
            registry.get_irrigator(_irrigator(device_type=raw_type))
        assert str(excinfo.value) == f"No adapter registered for irrigator type {raw_type!r} (known: rainpoint.ik10pw)"

    @pytest.mark.parametrize("raw_type", ["", "soil_moisture", "temp_humidity", "light"])
    def test_legacy_and_empty_sensor_types_are_unknown(self, raw_type):
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        assert registry.get_sensor(_sensor(raw_type)) is None

    def test_unknown_irrigator_message_is_exact(self):
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        with pytest.raises(UnknownDeviceModel) as excinfo:
            registry.get_irrigator(_irrigator(device_type="acme.pump"))
        assert str(excinfo.value) == "No adapter registered for irrigator type 'acme.pump' (known: rainpoint.ik10pw)"

    def test_unknown_sensor_logs_warning_and_returns_none(self, caplog):
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.registry"):
            assert registry.get_sensor(_sensor("acme.probe")) is None
        assert caplog.messages == [
            "No adapter registered for sensor type 'acme.probe' (known: tuya.tr301z); sensor 9 will be skipped"
        ]

    def test_empty_registry_and_late_registration_order(self):
        registry = DeviceRegistry()
        assert registry.registered_irrigator_keys() == ()
        registry.register_irrigator("b.x", lambda: None)
        registry.register_irrigator("a.y", lambda: None)
        assert registry.registered_irrigator_keys() == ("b.x", "a.y")


# ── DeviceGateway construction ────────────────────────────────────────────────


class TestGatewayConstruction:
    def test_builds_exactly_one_cloud_client_with_explicit_credentials(self, monkeypatch):
        built = []
        monkeypatch.setattr(
            "greenhouse_core.devices.tinytuya.Cloud", lambda **kw: built.append(kw) or SimpleNamespace(tag="cloud")
        )
        gateway = DeviceGateway("cid", "secret", "us")
        assert built == [{"apiRegion": "us", "apiKey": "cid", "apiSecret": "secret"}]
        assert gateway._cloud.tag == "cloud"
        assert (gateway.client_id, gateway.client_secret, gateway.region) == ("cid", "secret", "us")

    def test_environment_fallback_and_default_region(self, monkeypatch, clean_env):
        built = []
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: built.append(kw))
        monkeypatch.setenv("TUYA_CLIENT_ID", "env-id")
        monkeypatch.setenv("TUYA_CLIENT_SECRET", "env-secret")
        DeviceGateway()
        assert built == [{"apiRegion": "eu", "apiKey": "env-id", "apiSecret": "env-secret"}]

    def test_injected_raw_client_skips_construction(self, monkeypatch):
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: pytest.fail("must not build"))
        raw = object()
        assert DeviceGateway("cid", "secret", raw=raw)._cloud is raw

    @pytest.mark.parametrize(("cid", "secret"), [("", "s"), ("c", ""), (None, None)])
    def test_missing_credentials_fail_before_any_client(self, monkeypatch, clean_env, cid, secret):
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: pytest.fail("must not build"))
        with pytest.raises(ValueError) as excinfo:
            DeviceGateway(cid, secret)
        assert str(excinfo.value) == "Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET"


# ── Invariant #8: single-call live read ───────────────────────────────────────


class TestLiveReadSingleCall:
    def test_v2_success_is_one_call(self, harness):
        harness.cloud.set(
            "cloudrequest",
            {
                "success": True,
                "result": {
                    "properties": [
                        {"code": "temp_current", "value": 231},
                        {"code": "humidity", "value": 44},
                        {"code": "unknown_dp", "value": 1},
                        {"code": "water_warning", "value": 1},
                    ]
                },
            },
        )
        data = harness.gateway.get_live_reading(FAKE_SENSOR_ID)
        assert data == {"temperature": 23.1, "soil_moisture": 44.0, "water_warning": True}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]

    @pytest.mark.parametrize(
        "shadow",
        [{"success": True, "result": {"properties": []}}, {"success": True, "result": {}}, {"success": True}],
    )
    def test_v2_empty_but_successful_is_authoritative(self, harness, shadow):
        harness.cloud.set("cloudrequest", shadow)
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]

    @pytest.mark.parametrize(
        "v2_failure",
        [{"success": False}, {}, None, ConnectionError("dns"), RuntimeError("boom")],
        ids=["success-false", "no-success-key", "none", "connection-error", "runtime-error"],
    )
    def test_v1_getstatus_only_when_v2_fails(self, harness, v2_failure):
        harness.cloud.set("cloudrequest", v2_failure)
        harness.cloud.set("getstatus", {"success": True, "result": [{"code": "va_temperature", "value": "205"}]})
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {"temperature": 20.5}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH), ("cloud.getstatus", FAKE_SENSOR_ID)]

    def test_both_endpoints_failing_raises_with_v1_payload(self, harness):
        harness.cloud.set("cloudrequest", {"success": False})
        harness.cloud.set("getstatus", {"success": False, "msg": "offline"})
        with pytest.raises(RuntimeError) as excinfo:
            harness.gateway.get_live_reading(FAKE_SENSOR_ID)
        assert str(excinfo.value) == "Cloud API error: {'success': False, 'msg': 'offline'}"

    def test_v1_transport_error_propagates(self, harness):
        harness.cloud.set("cloudrequest", ConnectionError("v2 down"))
        harness.cloud.set("getstatus", ConnectionError("v1 down"))
        with pytest.raises(ConnectionError, match="v1 down"):
            harness.gateway.get_live_reading(FAKE_SENSOR_ID)

    def test_v2_parser_error_is_not_caught(self, harness):
        """The v2 path does not guard parser errors (get_device_logs does) — pinned."""
        harness.cloud.set("cloudrequest", {"success": True, "result": {"properties": [{"code": "humidity"}]}})
        with pytest.raises(TypeError):
            harness.gateway.get_live_reading(FAKE_SENSOR_ID)
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]

    def test_later_dp_wins_for_the_same_canonical_key(self, harness):
        harness.cloud.set(
            "cloudrequest",
            {
                "success": True,
                "result": {
                    "properties": [{"code": "temp_current", "value": 100}, {"code": "va_temperature", "value": 200}]
                },
            },
        )
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {"temperature": 20.0}


# ── getdevicelog + grouping ───────────────────────────────────────────────────


class TestDeviceLogs:
    def test_exact_request_parse_and_sort(self, harness):
        harness.cloud.set(
            "getdevicelog",
            {
                "result": {
                    "logs": [
                        {"code": "humidity", "value": "51", "event_time": 1_700_000_003_000},
                        {"code": "temp_current", "value": "bad", "event_time": 1_700_000_002_500},
                        {"code": "mystery", "value": "x", "event_time": 1_700_000_001_000},
                        {"value": "7"},
                    ]
                }
            },
        )
        logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=123)
        assert harness.log == [
            ("cloud.getdevicelog", FAKE_SENSOR_ID, {"start": 123, "end": 0, "evtype": 7, "size": 100}),
        ]
        assert logs == [
            {"timestamp_ms": 0, "timestamp": 0, "code": "", "raw_value": "7", "key": "", "value": "7"},
            {
                "timestamp_ms": 1_700_000_001_000,
                "timestamp": 1_700_000_001,
                "code": "mystery",
                "raw_value": "x",
                "key": "mystery",
                "value": "x",
            },
            {
                "timestamp_ms": 1_700_000_002_500,
                "timestamp": 1_700_000_002,
                "code": "temp_current",
                "raw_value": "bad",
                "key": "temp_current",
                "value": "bad",
            },
            {
                "timestamp_ms": 1_700_000_003_000,
                "timestamp": 1_700_000_003,
                "code": "humidity",
                "raw_value": "51",
                "key": "soil_moisture",
                "value": 51.0,
            },
        ]

    def test_default_window_is_hours_back_from_now(self, harness):
        harness.cloud.set("getdevicelog", {"result": {"logs": []}})
        harness.gateway.get_device_logs(FAKE_SENSOR_ID)
        harness.gateway.get_device_logs(FAKE_SENSOR_ID, hours=2, max_records=5)
        assert harness.log == [
            (
                "cloud.getdevicelog",
                FAKE_SENSOR_ID,
                {"start": (FROZEN_TS - 24 * 3600) * 1000, "end": 0, "evtype": 7, "size": 100},
            ),
            (
                "cloud.getdevicelog",
                FAKE_SENSOR_ID,
                {"start": (FROZEN_TS - 2 * 3600) * 1000, "end": 0, "evtype": 7, "size": 5},
            ),
        ]

    @pytest.mark.parametrize("payload", [{}, {"result": {}}, {"success": False}])
    def test_missing_logs_is_empty(self, harness, payload):
        harness.cloud.set("getdevicelog", payload)
        assert harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0) == []

    def test_group_logs_tolerance_boundary_and_timestamp_rules(self):
        logs = [
            {"timestamp_ms": 10_000, "timestamp": 10, "key": "temperature", "value": 20.0},
            {"timestamp_ms": 15_000, "timestamp": 15, "key": "soil_moisture", "value": 40.0},  # +5000: same group
            {"timestamp_ms": 15_001, "timestamp": 15, "key": "light", "value": 9},  # 5001 from group start: new
            {"timestamp_ms": 40_000, "timestamp": 40},  # no key → group of timestamp only
            {"timestamp_ms": 90_000, "timestamp": 90, "key": "temperature", "value": 21.0},
            {"timestamp_ms": 99_000, "timestamp": 99},  # trailing key-less group is dropped
        ]
        assert gateway_module.group_logs_by_timestamp(logs) == [
            {"timestamp": 15, "temperature": 20.0, "soil_moisture": 40.0},
            {"timestamp": 15, "light": 9},
            {"timestamp": 40},  # an intermediate key-less group is kept (only the last is filtered)
            {"timestamp": 90, "temperature": 21.0},
        ]
        assert DeviceGateway.group_logs_by_timestamp(logs, tolerance_ms=100_000) == [
            {"timestamp": 90, "temperature": 21.0, "soil_moisture": 40.0, "light": 9}
        ]
        assert gateway_module.group_logs_by_timestamp([]) == []


# ── Cloud commands ────────────────────────────────────────────────────────────


class TestCloudCommands:
    def test_send_command_success_and_failure(self, harness):
        harness.cloud.set("sendcommand", Seq({"success": True, "t": 1}, {"success": False, "code": 2008}))
        assert harness.gateway.send_command(FAKE_DEVICE_ID, SWITCH_ON) == (True, "Command succeeded")
        assert harness.gateway.send_command(FAKE_DEVICE_ID, SWITCH_OFF) == (
            False,
            "Cloud API error: {'success': False, 'code': 2008}",
        )
        assert harness.log == [
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_OFF),
        ]

    def test_get_status_is_a_raw_passthrough(self, harness):
        harness.cloud.set("getstatus", {"anything": [1]})
        assert harness.gateway.get_status(FAKE_DEVICE_ID) == {"anything": [1]}
        assert harness.log == [("cloud.getstatus", FAKE_DEVICE_ID)]


# ── Invariant #8: local key resolution without the Cloud ──────────────────────


class TestLocalKeyResolution:
    @pytest.mark.parametrize(
        "config",
        [
            {"device_ip": FAKE_DEVICE_IP, "local_key": FAKE_LOCAL_KEY},
            f'{{"device_ip": "{FAKE_DEVICE_IP}", "local_key": "{FAKE_LOCAL_KEY}"}}',
        ],
        ids=["dict-config", "json-string-config"],
    )
    def test_open_local_from_config_costs_zero_cloud_calls(self, harness, config):
        harness.gateway.open_local(_irrigator(config), 3.5)
        assert harness.log == _local_open_calls()
        assert harness.cloud_calls() == []

    def test_cold_lookup_once_then_process_cache(self, harness):
        discovered = []
        gateway = DeviceGateway(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=harness.cloud, on_key_discovered=lambda *a: discovered.append(a)
        )
        harness.cloud.set("getdevices", [{"id": "other", "key": "nope"}, {"id": FAKE_DEVICE_ID, "key": "cold-key"}])
        irr = _irrigator({"device_ip": FAKE_DEVICE_IP})
        gateway.open_local(irr, 3.5)
        gateway.open_local(irr, 3.5)
        assert harness.log == [("cloud.getdevices",), *_local_open_calls("cold-key"), *_local_open_calls("cold-key")]
        assert discovered == [(FAKE_DEVICE_ID, "cold-key")]
        assert gateway._key_cache == {FAKE_DEVICE_ID: "cold-key"}

    def test_refresh_forces_cloud_even_with_config_key(self, harness):
        harness.cloud.set("getdevices", [{"id": FAKE_DEVICE_ID, "key": "rotated"}])
        harness.gateway.open_local(_irrigator(), 3.5, refresh=True)
        assert harness.log == [("cloud.getdevices",), *_local_open_calls("rotated")]

    def test_resolve_local_key_order_and_invalidate(self, harness):
        gw = harness.gateway
        harness.cloud.set("getdevices", [{"id": FAKE_DEVICE_ID, "key": "from-cloud"}])
        assert gw.resolve_local_key(FAKE_DEVICE_ID, {"local_key": "cfg"}) == "cfg"
        assert gw.resolve_local_key(FAKE_DEVICE_ID, "not json") == "from-cloud"  # garbage config → cold path
        assert gw.resolve_local_key(FAKE_DEVICE_ID, None) == "from-cloud"  # cache hit
        assert gw.resolve_local_key(FAKE_DEVICE_ID, {"local_key": ""}) == "from-cloud"  # empty cfg key → cache
        gw.invalidate_key(FAKE_DEVICE_ID)
        gw.invalidate_key("never-cached")  # no KeyError
        assert gw.resolve_local_key(FAKE_DEVICE_ID, ["not", "a", "dict"]) == "from-cloud"
        assert harness.log == [("cloud.getdevices",), ("cloud.getdevices",)]

    @pytest.mark.parametrize("devices", [[{"id": "someone-else", "key": "k"}], {"error": "not a list"}, []])
    def test_unknown_device_resolves_none_and_is_not_cached(self, harness, devices):
        harness.cloud.set("getdevices", devices)
        assert harness.gateway.resolve_local_key(FAKE_DEVICE_ID) is None
        assert harness.gateway._key_cache == {}

    def test_persistence_hook_failure_is_logged_not_raised(self, harness, caplog):
        def _boom(device_id, key):
            raise RuntimeError("db locked")

        gateway = DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=harness.cloud, on_key_discovered=_boom)
        harness.cloud.set("getdevices", [{"id": FAKE_DEVICE_ID, "key": "k1"}])
        with caplog.at_level(logging.ERROR, logger="greenhouse_core.devices.gateway"):
            assert gateway.resolve_local_key(FAKE_DEVICE_ID) == "k1"
        assert caplog.messages == [f"local_key persistence hook failed for {FAKE_DEVICE_ID}"]
        assert gateway._key_cache == {FAKE_DEVICE_ID: "k1"}

    def test_open_local_without_ip_never_touches_cloud(self, harness):
        with pytest.raises(ConnectionError) as excinfo:
            harness.gateway.open_local(_irrigator({"local_key": FAKE_LOCAL_KEY}), 3.5)
        assert str(excinfo.value) == f"No device_ip in config for irrigator {FAKE_DEVICE_ID}."
        assert harness.log == []

    def test_open_local_without_any_key_raises_after_one_lookup(self, harness):
        harness.cloud.set("getdevices", [])
        with pytest.raises(ConnectionError) as excinfo:
            harness.gateway.open_local(_irrigator({"device_ip": FAKE_DEVICE_IP}), 3.5)
        assert str(excinfo.value) == f"Could not find local key for device {FAKE_DEVICE_ID}"
        assert harness.log == [("cloud.getdevices",)]


# ── Adapter call sequences (broad golden) ─────────────────────────────────────


def _scenarios():
    """name → (setup(harness), action(harness) -> result). Keep-alive paths are tested separately."""
    ok = {"success": True}
    fail = {"success": False, "msg": "offline"}

    def ik(h):
        return IK10PWAdapter(h.gateway)

    def generic_cloud(h):
        profile = IrrigatorProfile(
            model_key="tuya.generic", vendor="tuya", transport="tuya_cloud", protocol_version=None
        )
        return TuyaIrrigatorAdapter(profile, h.gateway)

    def generic_local_no_cap(h):
        profile = IrrigatorProfile(
            model_key="tuya.local", vendor="tuya", transport="tuya_local", protocol_version=3.4, dp_map={"switch": 1}
        )
        return TuyaIrrigatorAdapter(profile, h.gateway)

    cloud_status = {
        "success": True,
        "result": [
            {"code": "switch", "value": True},
            {"code": "work_state", "value": "auto"},
            {"code": "x", "value": 1},
        ],
    }
    local_dps = {"dps": {"1": True, "102": 300, "104": 120, "105": 0, "106": 2, "109": False, "108": 1}}

    return {
        "ik10pw.on.ok": (lambda h: h.cloud.set("sendcommand", ok), lambda h: ik(h).on(_irrigator())),
        "ik10pw.on.cloud_rejects": (lambda h: h.cloud.set("sendcommand", fail), lambda h: ik(h).on(_irrigator())),
        "ik10pw.off.ok": (lambda h: h.cloud.set("sendcommand", ok), lambda h: ik(h).off(_irrigator())),
        "ik10pw.off.cloud_rejects": (lambda h: h.cloud.set("sendcommand", fail), lambda h: ik(h).off(_irrigator())),
        "ik10pw.stop.ok": (lambda h: h.cloud.set("sendcommand", ok), lambda h: ik(h).stop(_irrigator())),
        "ik10pw.start.unbounded": (lambda h: h.cloud.set("sendcommand", ok), lambda h: ik(h).start(_irrigator(), None)),
        "ik10pw.start.5min.local_dp_then_cloud_switch": (
            lambda h: h.cloud.set("sendcommand", ok),
            lambda h: ik(h).start(_irrigator(), 5),
        ),
        "ik10pw.start.fractional_minutes": (
            lambda h: h.cloud.set("sendcommand", ok),
            lambda h: ik(h).start(_irrigator(), 2.5),
        ),
        "ik10pw.start.local_dp_ok_cloud_switch_fails": (
            lambda h: h.cloud.set("sendcommand", fail),
            lambda h: ik(h).start(_irrigator(), 5),
        ),
        "ik10pw.start.local_set_value_returns_none_counts_as_ok": (
            lambda h: (h.cloud.set("sendcommand", ok), setattr(h, "local_set_value", None)),
            lambda h: ik(h).start(_irrigator(), 1),
        ),
        "ik10pw.set_duration_local.error_payload": (
            lambda h: setattr(h, "local_set_value", {"Error": "Network Error: Device Unreachable", "Err": "905"}),
            lambda h: ik(h)._set_duration_local(_irrigator(), 60),
        ),
        "ik10pw.set_duration_local.no_ip": (
            lambda h: None,
            lambda h: ik(h)._set_duration_local(_irrigator({}), 60),
        ),
        "ik10pw.set_duration_local.ok_payload": (
            lambda h: setattr(h, "local_set_value", {"dps": {"102": 60}}),
            lambda h: ik(h)._set_duration_local(_irrigator(), 60),
        ),
        "ik10pw.status.local": (
            lambda h: setattr(h, "local_status", local_dps),
            lambda h: ik(h).status(_irrigator()),
        ),
        "ik10pw.status.local_without_dps_falls_back_to_cloud": (
            lambda h: (setattr(h, "local_status", {"Error": "timeout"}), h.cloud.set("getstatus", cloud_status)),
            lambda h: ik(h).status(_irrigator()),
        ),
        "ik10pw.status.local_raises_falls_back_to_cloud": (
            lambda h: (
                setattr(h, "local_status_error", OSError("reset by peer")),
                h.cloud.set("getstatus", cloud_status),
            ),
            lambda h: ik(h).status(_irrigator()),
        ),
        "ik10pw.status.no_ip_cloud_fails": (
            lambda h: h.cloud.set("getstatus", fail),
            lambda h: ik(h).status(_irrigator({})),
        ),
        "ik10pw.read_health.no_water_bit": (
            lambda h: setattr(h, "local_status", {"dps": {"1": True, "104": 30, "105": 3, "106": 1}}),
            lambda h: _state_dict(ik(h).read_health(_irrigator())),
        ),
        "ik10pw.read_health.string_dp_clear": (
            lambda h: setattr(h, "local_status", {"dps": {"105": "2"}}),
            lambda h: _state_dict(ik(h).read_health(_irrigator())),
        ),
        "ik10pw.read_health.status_none": (
            lambda h: setattr(h, "local_status", None),
            lambda h: _state_dict(ik(h).read_health(_irrigator())),
        ),
        "ik10pw.read_health.local_raises_is_offline": (
            lambda h: setattr(h, "local_status_error", TimeoutError("timed out")),
            lambda h: _state_dict(ik(h).read_health(_irrigator())),
        ),
        "ik10pw.read_health.no_ip_is_offline": (
            lambda h: None,
            lambda h: _state_dict(ik(h).read_health(_irrigator({}))),
        ),
        "generic_cloud.start_ignores_minutes": (
            lambda h: h.cloud.set("sendcommand", ok),
            lambda h: generic_cloud(h).start(_irrigator(), 5),
        ),
        "generic_cloud.stop": (lambda h: h.cloud.set("sendcommand", ok), lambda h: generic_cloud(h).stop(_irrigator())),
        "generic_cloud.status_skips_local": (
            lambda h: h.cloud.set("getstatus", cloud_status),
            lambda h: generic_cloud(h).status(_irrigator()),
        ),
        "generic_local_without_capability.status_skips_local": (
            lambda h: h.cloud.set("getstatus", cloud_status),
            lambda h: generic_local_no_cap(h).status(_irrigator()),
        ),
        "generic_cloud.read_health_clean": (
            lambda h: None,
            lambda h: _state_dict(generic_cloud(h).read_health(_irrigator())),
        ),
        "tr301z.read_live.v2": (
            lambda h: h.cloud.set(
                "cloudrequest",
                {"success": True, "result": {"properties": [{"code": "humidity_value", "value": 38}]}},
            ),
            lambda h: TR301ZAdapter(h.gateway).read_live(_sensor()),
        ),
        "tr301z.read_live.both_fail_returns_error_dict": (
            lambda h: (h.cloud.set("cloudrequest", fail), h.cloud.set("getstatus", fail)),
            lambda h: TR301ZAdapter(h.gateway).read_live(_sensor()),
        ),
        "tr301z.read_live.transport_error_returns_error_dict": (
            lambda h: (h.cloud.set("cloudrequest", OSError("x")), h.cloud.set("getstatus", OSError("no route"))),
            lambda h: TR301ZAdapter(h.gateway).read_live(_sensor()),
        ),
    }


def test_adapter_call_sequences_golden(monkeypatch, frozen_clock):
    """Every non-keep-alive adapter path: return value and the exact local/Cloud call log."""
    out = {}
    for name, (setup, action) in _scenarios().items():
        h = Harness(monkeypatch)
        setup(h)
        result = action(h)
        out[name] = {"result": result, "calls": h.log}
    assert_golden_json(f"{GOLDEN}/adapter_call_sequences.json", out)


class TestAdapterInvariants:
    def test_ik10pw_bounded_start_is_local_dp102_over_3_5_then_cloud_switch(self, harness):
        harness.cloud.set("sendcommand", {"success": True})
        result = IK10PWAdapter(harness.gateway).start(_irrigator(), 5)
        assert result == (True, "Irrigation started for 5 min (Duration DP set to 300s, device auto-stops)")
        assert harness.log == [
            *_local_open_calls(),
            ("local.set_value", 102, 300),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
        ]

    def test_ik10pw_local_paths_use_profile_protocol_version(self, harness, monkeypatch):
        seen = []
        real = harness.gateway.open_local
        monkeypatch.setattr(
            harness.gateway, "open_local", lambda irr, proto, **kw: seen.append(proto) or real(irr, proto)
        )
        harness.cloud.set("sendcommand", {"success": True})
        adapter = IK10PWAdapter(harness.gateway)
        adapter.start(_irrigator(), 1)
        adapter.status(_irrigator())
        adapter.read_health(_irrigator())
        assert seen == [3.5, 3.5, 3.5]

    def test_switch_pulse_goes_via_cloud_only(self, harness):
        harness.cloud.set("sendcommand", {"success": True})
        adapter = IK10PWAdapter(harness.gateway)
        adapter.on(_irrigator())
        adapter.off(_irrigator())
        assert harness.log == [
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_OFF),
        ]

    def test_cloud_switch_transport_error_propagates(self, harness):
        harness.cloud.set("sendcommand", ConnectionError("unreachable"))
        with pytest.raises(ConnectionError):
            IK10PWAdapter(harness.gateway).on(_irrigator())

    @pytest.mark.parametrize(
        ("bitmask", "raw", "tripped"), [(None, 1, True), (2, 1, False), (2, 2, True), (2, 3, True)]
    )
    def test_read_health_uses_profile_bitmask_defaulting_to_bit0(self, harness, bitmask, raw, tripped):
        profile = dataclasses.replace(IK10PW_PROFILE, alarm_bitmask=bitmask)
        harness.local_status = {"dps": {"105": raw}}
        state = IK10PWAdapter(harness.gateway, profile).read_health(_irrigator())
        assert (HealthAlarm.NO_WATER in state.alarms) is tripped


@pytest.mark.parametrize(
    ("latest", "expected"),
    [
        (None, {"offline": True, "battery_pct": None, "alarms": [], "last_seen_ts": None}),
        (_reading(battery_state="low"), {"offline": False, "battery_pct": 10, "alarms": []}),
        (_reading(battery_state="Middle"), {"offline": False, "battery_pct": 50, "alarms": []}),
        (_reading(battery_state="HIGH"), {"offline": False, "battery_pct": 90, "alarms": []}),
        (_reading(battery_state="full"), {"offline": False, "battery_pct": None, "alarms": []}),
        (_reading(battery_state=None), {"offline": False, "battery_pct": None, "alarms": []}),
        (_reading(water_warning=True), {"alarms": ["sensor_fault"]}),
        (_reading(water_warning=False), {"alarms": []}),
        (_reading(water_warning=None), {"alarms": []}),
        (_reading(timestamp=123), {"last_seen_ts": 123}),
    ],
)
def test_tr301z_read_health_derives_from_persisted_row_with_zero_cloud_calls(harness, latest, expected):
    state = TR301ZAdapter(harness.gateway).read_health(_sensor(), latest)
    got = _state_dict(state)
    assert got["observed_at"] == FROZEN_TS
    assert {k: got[k] for k in expected} == expected
    assert state.signal_quality is None
    assert state.alarms <= TR301ZAdapter.health_capabilities
    assert harness.log == []


def test_tr301z_read_health_raw_payloads(harness):
    adapter = TR301ZAdapter(harness.gateway)
    assert adapter.read_health(_sensor(), None).raw == {"reason": "no persisted reading"}
    row = _reading(battery_state="low", water_warning=True)
    assert adapter.read_health(_sensor(), row).raw == {
        "battery_state": "low",
        "water_warning": True,
        "reading_ts": FROZEN_TS - 600,
    }
    assert _state_dict(TuyaSensorAdapter(TR301Z_PROFILE, harness.gateway).read_health(_sensor(), row)) == {
        "observed_at": FROZEN_TS,
        "battery_pct": None,
        "signal_quality": None,
        "last_seen_ts": None,
        "offline": False,
        "alarms": [],
        "raw": {},
    }
    assert harness.log == []


# ── Keep-alive fallback (IK10PW) incl. pinned bug B-2 ─────────────────────────


@pytest.fixture
def fake_sleep(monkeypatch):
    """Replace the ``time`` module seen by ik10pw with an instant, recording sleep."""
    sleeps: list[float] = []
    monkeypatch.setattr(ik10pw_module, "time", SimpleNamespace(sleep=sleeps.append, time=time.time))
    return sleeps


def _keepalive_ok_harness(harness):
    harness.cloud.set("sendcommand", {"success": True})
    return IK10PWAdapter(harness.gateway)


class TestKeepAliveMainThread:
    def test_main_thread_cycle_pulses_on_and_always_switches_off(self, harness, fake_sleep):
        assert threading.current_thread() is threading.main_thread()
        before = signal.getsignal(signal.SIGTERM)
        result = _keepalive_ok_harness(harness)._start_keepalive(_irrigator(), 1)
        assert result == (True, "Irrigation completed for 1 min [keep-alive mode]")
        assert fake_sleep == [20, 20, 20]
        assert harness.log == [
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_OFF),
        ]
        assert signal.getsignal(signal.SIGTERM) is before

    def test_interval_patched_last_sleep_is_the_remainder(self, harness, fake_sleep, monkeypatch):
        monkeypatch.setattr(ik10pw_module, "KEEP_ALIVE_INTERVAL", 25)
        result = _keepalive_ok_harness(harness)._start_keepalive(_irrigator(), 1)
        assert result == (True, "Irrigation completed for 1 min [keep-alive mode]")
        assert fake_sleep == [25, 25, 10]
        assert [c[2] for c in harness.log] == [SWITCH_ON, SWITCH_ON, SWITCH_ON, SWITCH_OFF]

    def test_start_falls_back_to_keepalive_when_local_dp_fails(self, harness, fake_sleep, caplog):
        adapter = _keepalive_ok_harness(harness)
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.irrigators.ik10pw"):
            result = adapter.start(_irrigator({}), 1)  # no device_ip → local fails without any I/O
        assert result == (True, "Irrigation completed for 1 min [keep-alive mode]")
        assert caplog.messages == [
            "Local Duration set failed (Local connection failed: No device_ip in config for irrigator "
            f"{FAKE_DEVICE_ID}.), using keep-alive fallback"
        ]
        assert [c[0] for c in harness.log] == ["cloud.sendcommand"] * 4
        assert harness.log[-1][2] == SWITCH_OFF

    def test_sigterm_during_cycle_switches_off_twice_and_reports_interrupted(self, harness, monkeypatch):
        before = signal.getsignal(signal.SIGTERM)
        sleeps: list[float] = []

        def _sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # deliver "SIGTERM" synchronously

        monkeypatch.setattr(ik10pw_module, "time", SimpleNamespace(sleep=_sleep, time=time.time))
        result = _keepalive_ok_harness(harness)._start_keepalive(_irrigator(), 1)
        assert result == (
            True,
            "Irrigation interrupted after ~20s — device turned off (requested 1 min) [keep-alive mode]",
        )
        # on, keep-alive on, off from the handler, off again from ``finally``.
        assert [c[2] for c in harness.log] == [SWITCH_ON, SWITCH_ON, SWITCH_OFF, SWITCH_OFF]
        assert signal.getsignal(signal.SIGTERM) is before

    def test_keyboard_interrupt_is_swallowed_and_switches_off_once(self, harness, monkeypatch):
        def _sleep(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(ik10pw_module, "time", SimpleNamespace(sleep=_sleep, time=time.time))
        result = _keepalive_ok_harness(harness)._start_keepalive(_irrigator(), 1)
        assert result == (
            True,
            "Irrigation interrupted after ~0s — device turned off (requested 1 min) [keep-alive mode]",
        )
        assert [c[2] for c in harness.log] == [SWITCH_ON, SWITCH_OFF]

    def test_initial_on_failure_returns_before_signal_or_sleep(self, harness, fake_sleep):
        before = signal.getsignal(signal.SIGTERM)
        harness.cloud.set("sendcommand", {"success": False})
        result = IK10PWAdapter(harness.gateway)._start_keepalive(_irrigator(), 1)
        assert result == (False, "Failed to start irrigation: Failed to turn ON device")
        assert fake_sleep == []
        assert harness.log == [("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON)]
        assert signal.getsignal(signal.SIGTERM) is before

    def test_keepalive_and_final_off_errors_are_swallowed(self, harness, fake_sleep):
        harness.cloud.set(
            "sendcommand", Seq({"success": True}, ConnectionError("blip"), {"success": True}, ConnectionError("down"))
        )
        result = IK10PWAdapter(harness.gateway)._start_keepalive(_irrigator(), 1)
        assert result == (True, "Irrigation completed for 1 min [keep-alive mode]")
        assert [c[2] for c in harness.log] == [SWITCH_ON, SWITCH_ON, SWITCH_ON, SWITCH_OFF]

    def test_zero_minutes_still_switches_on_then_off(self, harness, fake_sleep):
        result = _keepalive_ok_harness(harness)._start_keepalive(_irrigator(), 0)
        assert result == (True, "Irrigation completed for 0 min [keep-alive mode]")
        assert fake_sleep == []
        assert [c[2] for c in harness.log] == [SWITCH_ON, SWITCH_OFF]


def _run_in_thread(fn) -> dict:
    outcome: dict = {}

    def _target():
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — the exception is what we pin
            outcome["exc"] = exc

    worker = threading.Thread(target=_target, name="fastapi-threadpool-stand-in")
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    return outcome


def test_keepalive_off_main_thread_current_behavior_leaves_pump_on(harness, fake_sleep):
    """Pins current (buggy) behavior: B-2 — see REFACTOR_NOTES.md.

    ``_start_keepalive`` switches the pump ON via the Cloud, then calls
    ``signal.signal(SIGTERM, …)`` *outside* its ``try``. Off the main thread
    (FastAPI threadpool, APScheduler worker) that raises ``ValueError``, so the
    ``finally`` that sends OFF never runs: the pump is left on, bounded only by
    the device's own ~30 s auto-off, and the error propagates to the caller.
    """
    before = signal.getsignal(signal.SIGTERM)
    adapter = _keepalive_ok_harness(harness)
    outcome = _run_in_thread(lambda: adapter._start_keepalive(_irrigator(), 1))
    assert "result" not in outcome
    assert isinstance(outcome["exc"], ValueError)
    assert "main thread" in str(outcome["exc"])
    assert harness.log == [("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON)]  # ON sent, OFF never sent
    assert fake_sleep == []
    assert signal.getsignal(signal.SIGTERM) is before


def test_start_off_main_thread_current_behavior_raises_after_switching_on(harness, fake_sleep):
    """Pins current (buggy) behavior: B-2 via the public ``start`` — see REFACTOR_NOTES.md.

    A bounded ``start`` whose local Duration write fails falls back to keep-alive;
    on a worker thread that ends in ``ValueError`` after the Cloud ON pulse.
    """
    adapter = _keepalive_ok_harness(harness)
    outcome = _run_in_thread(lambda: adapter.start(_irrigator({}), 3))
    assert isinstance(outcome.get("exc"), ValueError)
    assert harness.log == [("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON)]


# ── alarm DP parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "bitmask", "expected"),
    [
        (True, 0x02, True),  # bool is checked before int — mask ignored
        (False, 0x01, False),
        (2, 0x02, True),
        (1, 0x02, False),
        (-1, 0x01, True),
        (" 3 ", 0x02, True),
        ("0x01", 0x01, False),  # not decimal → unknown → False
        (1.0, 0x01, False),  # floats are an unrecognised type
        (b"1", 0x01, False),
    ],
)
def test_alarm_indicates_no_water_with_bitmask(value, bitmask, expected):
    assert alarm_indicates_no_water(value, bitmask) is expected


@settings(derandomize=True, deadline=None, max_examples=200)
@given(value=st.integers(min_value=-(2**40), max_value=2**40), bitmask=st.integers(min_value=0, max_value=255))
def test_alarm_int_and_decimal_string_agree_with_bitwise_and(value, bitmask):
    expected = bool(value & bitmask)
    assert alarm_indicates_no_water(value, bitmask) is expected
    assert alarm_indicates_no_water(str(value), bitmask) is expected
