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
bounded IK10PW start refuses before any ON when the local Duration DP cannot be
written — S2 / B-2: the keep-alive fallback is gone).
"""

from __future__ import annotations

import dataclasses
import logging
import signal
import threading
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
        """Queue ``items`` to be returned one per call, in order."""
        self.items = list(items)


class RecordingCloud:
    """Stand-in for ``tinytuya.Cloud``: records every call, replays canned responses.

    A response may be a value, an exception instance (raised), or a :class:`Seq`
    (popped in order; the last entry repeats). Unconfigured methods raise so an unexpected
    Cloud call can never pass silently.
    """

    def __init__(self, log: list) -> None:
        """Record every call into the shared ``log``; responses are set per method."""
        self.log = log
        self.responses: dict[str, object] = {}

    def set(self, method: str, response: object) -> None:
        """Script the response (or exception, or ``Seq``) the next ``method`` calls return."""
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
        """Record a v2.0 shadow request and return its scripted response."""
        self.log.append(("cloud.cloudrequest", url))
        return self._respond("cloudrequest")

    def getstatus(self, device_id):
        """Record a v1.0 status read and return its scripted response."""
        self.log.append(("cloud.getstatus", device_id))
        return self._respond("getstatus")

    def getdevicelog(self, device_id, **kwargs):
        """Record a device-log page request and return its scripted response."""
        self.log.append(("cloud.getdevicelog", device_id, kwargs))
        return self._respond("getdevicelog")

    def sendcommand(self, device_id, commands):
        """Record a Cloud command and return its scripted response."""
        self.log.append(("cloud.sendcommand", device_id, commands))
        return self._respond("sendcommand")

    def getdevices(self):
        """Record a device listing (the cold local-key lookup) and return its scripted response."""
        self.log.append(("cloud.getdevices",))
        return self._respond("getdevices")


class Harness:
    """One recording Cloud + recording local-device class sharing a call log."""

    def __init__(self, monkeypatch) -> None:
        """Wire a recording Cloud and a fake local tinytuya device into one shared call log."""
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
        """Return only the Cloud calls from the shared log."""
        return [c for c in self.log if c[0].startswith("cloud.")]


@pytest.fixture
def harness(monkeypatch, frozen_clock) -> Harness:
    """Provide a recording harness with the clock frozen at ``FROZEN_TS``."""
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
    """Profiles reject unknown DP names and answer capability lookups exactly."""
    with pytest.raises(KeyError):
        IK10PW_PROFILE.dp("rain_sensor")
    assert IK10PW_PROFILE.has_capability("supports_local_status") is True
    assert IK10PW_PROFILE.has_capability("nope") is False
    assert TR301Z_PROFILE.has_capability("reports_battery") is True


class TestRegistryResolution:
    """Registry: model key to adapter resolution."""

    def test_factories_build_a_fresh_adapter_per_lookup_sharing_one_gateway(self):
        """Each lookup builds a fresh adapter, but all of them share the one app gateway (invariant 8)."""
        gateway = DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object())
        registry = build_default_registry(gateway)
        a = registry.get_irrigator(_irrigator())
        b = registry.get_irrigator(_irrigator())
        s = registry.get_sensor(_sensor())
        assert type(a) is IK10PWAdapter and type(s) is TR301ZAdapter
        assert a is not b
        assert a._gateway is gateway and b._gateway is gateway and s._gateway is gateway

    def test_only_the_model_key_resolves_to_ik10pw(self):
        """Only the exact model key resolves to the IK10PW adapter."""
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
        """Legacy and empty sensor types resolve to no adapter."""
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        assert registry.get_sensor(_sensor(raw_type)) is None

    def test_unknown_irrigator_message_is_exact(self):
        """An unknown irrigator type raises with the exact message naming the known keys."""
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        with pytest.raises(UnknownDeviceModel) as excinfo:
            registry.get_irrigator(_irrigator(device_type="acme.pump"))
        assert str(excinfo.value) == "No adapter registered for irrigator type 'acme.pump' (known: rainpoint.ik10pw)"

    def test_unknown_sensor_logs_warning_and_returns_none(self, caplog):
        """An unknown sensor type is skipped with one exact warning instead of raising."""
        registry = build_default_registry(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=object()))
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.registry"):
            assert registry.get_sensor(_sensor("acme.probe")) is None
        assert caplog.messages == [
            "No adapter registered for sensor type 'acme.probe' (known: tuya.tr301z); sensor 9 will be skipped"
        ]

    def test_empty_registry_and_late_registration_order(self):
        """Registered keys are reported in registration order, starting empty."""
        registry = DeviceRegistry()
        assert registry.registered_irrigator_keys() == ()
        registry.register_irrigator("b.x", lambda: None)
        registry.register_irrigator("a.y", lambda: None)
        assert registry.registered_irrigator_keys() == ("b.x", "a.y")


# ── DeviceGateway construction ────────────────────────────────────────────────


class TestGatewayConstruction:
    """DeviceGateway construction: one Cloud client, credentials and region."""

    def test_builds_exactly_one_cloud_client_with_explicit_credentials(self, monkeypatch):
        """Explicit credentials build exactly one tinytuya Cloud client."""
        built = []
        monkeypatch.setattr(
            "greenhouse_core.devices.tinytuya.Cloud", lambda **kw: built.append(kw) or SimpleNamespace(tag="cloud")
        )
        gateway = DeviceGateway("cid", "secret", "us")
        assert built == [{"apiRegion": "us", "apiKey": "cid", "apiSecret": "secret"}]
        assert gateway._cloud.tag == "cloud"
        assert (gateway.client_id, gateway.client_secret, gateway.region) == ("cid", "secret", "us")

    def test_environment_fallback_and_default_region(self, monkeypatch, clean_env):
        """Without arguments, credentials come from the TUYA_ env vars and the region defaults to eu."""
        built = []
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: built.append(kw))
        monkeypatch.setenv("TUYA_CLIENT_ID", "env-id")
        monkeypatch.setenv("TUYA_CLIENT_SECRET", "env-secret")
        DeviceGateway()
        assert built == [{"apiRegion": "eu", "apiKey": "env-id", "apiSecret": "env-secret"}]

    def test_injected_raw_client_skips_construction(self, monkeypatch):
        """An injected raw client is used as is, never building a real one."""
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: pytest.fail("must not build"))
        raw = object()
        assert DeviceGateway("cid", "secret", raw=raw)._cloud is raw

    @pytest.mark.parametrize(("cid", "secret"), [("", "s"), ("c", ""), (None, None)])
    def test_missing_credentials_fail_before_any_client(self, monkeypatch, clean_env, cid, secret):
        """Missing credentials fail with an exact message before any client is built."""
        monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **kw: pytest.fail("must not build"))
        with pytest.raises(ValueError) as excinfo:
            DeviceGateway(cid, secret)
        assert str(excinfo.value) == "Missing TUYA_CLIENT_ID or TUYA_CLIENT_SECRET"


# ── Invariant #8: single-call live read ───────────────────────────────────────


class TestLiveReadSingleCall:
    """``get_live_reading`` is a single Cloud call unless v2.0 fails (invariant 8)."""

    def test_v2_success_is_one_call(self, harness):
        """A successful v2.0 shadow read is the only call and is parsed into canonical keys."""
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
        """An empty but successful v2.0 read is final: no v1.0 fallback."""
        harness.cloud.set("cloudrequest", shadow)
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]

    @pytest.mark.parametrize(
        "v2_failure",
        [{"success": False}, {}, None, ConnectionError("dns"), RuntimeError("boom")],
        ids=["success-false", "no-success-key", "none", "connection-error", "runtime-error"],
    )
    def test_v1_getstatus_only_when_v2_fails(self, harness, v2_failure):
        """v1.0 ``getstatus`` fires only after a failed v2.0 read."""
        harness.cloud.set("cloudrequest", v2_failure)
        harness.cloud.set("getstatus", {"success": True, "result": [{"code": "va_temperature", "value": "205"}]})
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {"temperature": 20.5}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH), ("cloud.getstatus", FAKE_SENSOR_ID)]

    def test_both_endpoints_failing_raises_with_v1_payload(self, harness):
        """When both endpoints fail, the error carries the v1.0 payload."""
        harness.cloud.set("cloudrequest", {"success": False})
        harness.cloud.set("getstatus", {"success": False, "msg": "offline"})
        with pytest.raises(RuntimeError) as excinfo:
            harness.gateway.get_live_reading(FAKE_SENSOR_ID)
        assert str(excinfo.value) == "Cloud API error: {'success': False, 'msg': 'offline'}"

    def test_v1_transport_error_propagates(self, harness):
        """A transport error on the v1.0 fallback propagates to the caller."""
        harness.cloud.set("cloudrequest", ConnectionError("v2 down"))
        harness.cloud.set("getstatus", ConnectionError("v1 down"))
        with pytest.raises(ConnectionError, match="v1 down"):
            harness.gateway.get_live_reading(FAKE_SENSOR_ID)

    def test_v2_parser_error_skips_only_that_dp(self, harness, caplog):
        """A DP its parser rejects is logged and skipped; the rest of the v2 reading is kept (one call)."""
        harness.cloud.set(
            "cloudrequest",
            {
                "success": True,
                "result": {"properties": [{"code": "humidity"}, {"code": "temp_current", "value": 231}]},
            },
        )
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.gateway"):
            assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {"temperature": 23.1}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]
        assert [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records] == [
            ("WARNING", f"Skipping unparseable DP humidity=None from {FAKE_SENSOR_ID}", True)
        ]

    def test_v1_parser_error_skips_only_that_dp(self, harness):
        """An unparseable v1.0 DP is dropped without losing the others."""
        harness.cloud.set("cloudrequest", {"success": False})
        harness.cloud.set(
            "getstatus",
            {"success": True, "result": [{"code": "va_temperature", "value": "205"}, {"code": "light", "value": "x"}]},
        )
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {"temperature": 20.5}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH), ("cloud.getstatus", FAKE_SENSOR_ID)]

    def test_v2_all_dps_unparseable_is_an_empty_authoritative_read(self, harness):
        """A v2.0 read whose DPs are all unparseable is still an authoritative empty read."""
        harness.cloud.set(
            "cloudrequest", {"success": True, "result": {"properties": [{"code": "light", "value": None}]}}
        )
        assert harness.gateway.get_live_reading(FAKE_SENSOR_ID) == {}
        assert harness.log == [("cloud.cloudrequest", SHADOW_PATH)]

    def test_later_dp_wins_for_the_same_canonical_key(self, harness):
        """When two DPs map to the same canonical key, the later one wins."""
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


def _log_page_kwargs(start: int, *, size: int = 100, start_row_key: str | None = None) -> dict:
    """The kwargs of one paginated ``getdevicelog`` request (tinytuya's own paging disabled, end fixed at now)."""
    return {
        "start": start,
        "end": FROZEN_TS * 1000,
        "evtype": 7,
        "size": size,
        "max_fetches": 1,
        "start_row_key": start_row_key,
    }


def _log_page(entries: list[tuple[str, str, int]], *, has_next: bool, next_row_key: str | None = None) -> dict:
    page: dict = {
        "logs": [{"code": code, "value": value, "event_time": ms} for code, value, ms in entries],
        "has_next": has_next,
    }
    if next_row_key is not None:
        page["next_row_key"] = next_row_key
    return {"success": True, "result": page}


class TestDeviceLogs:
    """``get_device_logs`` paging, parsing and grouping."""

    def test_exact_request_parse_and_sort(self, harness):
        """Device logs are requested with exact arguments, parsed and sorted by time."""
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
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs(123)),
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

    def test_logged_water_warning_strings_parse_to_booleans(self, harness):
        """``getdevicelog`` reports values as strings; DP 111 must read "false" as False."""
        harness.cloud.set(
            "getdevicelog",
            {
                "result": {
                    "logs": [
                        {"code": "water_warning", "value": "false", "event_time": 1_700_000_001_000},
                        {"code": "water_warning", "value": "true", "event_time": 1_700_000_002_000},
                    ]
                }
            },
        )
        logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0)
        assert [(e["key"], e["value"]) for e in logs] == [("water_warning", False), ("water_warning", True)]

    def test_default_window_is_hours_back_from_now(self, harness):
        """Without ``since_ms`` the window starts ``hours`` before now (24 by default)."""
        harness.cloud.set("getdevicelog", {"result": {"logs": []}})
        harness.gateway.get_device_logs(FAKE_SENSOR_ID)
        harness.gateway.get_device_logs(FAKE_SENSOR_ID, hours=2, page_size=5)
        assert harness.log == [
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs((FROZEN_TS - 24 * 3600) * 1000)),
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs((FROZEN_TS - 2 * 3600) * 1000, size=5)),
        ]

    def test_pages_through_the_window_with_the_row_key_cursor(self, harness):
        """B-20 fixed: ``has_next`` + ``next_row_key`` drive one request per page until the window is covered."""
        harness.cloud.set(
            "getdevicelog",
            Seq(
                _log_page([("humidity", "40", 3_000)], has_next=True, next_row_key="k1"),
                _log_page([("humidity", "41", 2_000)], has_next=True, next_row_key="k2"),
                _log_page([("humidity", "42", 1_000)], has_next=False),
            ),
        )
        logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=123)
        assert harness.log == [
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs(123)),
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs(123, start_row_key="k1")),
            ("cloud.getdevicelog", FAKE_SENSOR_ID, _log_page_kwargs(123, start_row_key="k2")),
        ]
        assert [(e["timestamp_ms"], e["value"]) for e in logs] == [(1_000, 42.0), (2_000, 41.0), (3_000, 40.0)]

    @pytest.mark.parametrize(
        "last_page",
        [{"result": {"logs": [], "has_next": True}}, {"result": {"logs": [], "has_next": True, "next_row_key": ""}}],
        ids=["no-row-key", "empty-row-key"],
    )
    def test_has_next_without_a_cursor_stops(self, harness, last_page):
        """A page claiming more data but giving no cursor ends the paging."""
        harness.cloud.set("getdevicelog", last_page)
        assert harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0) == []
        assert len(harness.log) == 1

    def test_repeated_cursor_stops_and_warns(self, harness, caplog):
        """A repeated cursor stops the paging with a warning instead of looping."""
        harness.cloud.set("getdevicelog", _log_page([("humidity", "40", 1_000)], has_next=True, next_row_key="again"))
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.gateway"):
            logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0)
        assert [c[2]["start_row_key"] for c in harness.log] == [None, "again"]
        assert len(logs) == 2
        assert caplog.messages == [f"getdevicelog for {FAKE_SENSOR_ID} repeated cursor 'again'; stopping"]

    def test_failed_page_stops_keeps_earlier_pages_and_warns(self, harness, caplog):
        """A failed page stops paging but keeps the pages already read."""
        harness.cloud.set(
            "getdevicelog",
            Seq(
                _log_page([("humidity", "40", 1_000)], has_next=True, next_row_key="k1"),
                {"success": False, "code": 1010},
            ),
        )
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.gateway"):
            logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0)
        assert len(harness.log) == 2
        assert [e["value"] for e in logs] == [40.0]
        assert caplog.messages == [f"getdevicelog page for {FAKE_SENSOR_ID} failed: {{'success': False, 'code': 1010}}"]

    def test_page_cap_bounds_the_requests_and_warns(self, harness, caplog):
        """The page cap bounds the number of requests and warns when it is hit."""
        harness.cloud.set(
            "getdevicelog",
            Seq(
                *(_log_page([("humidity", "40", 1_000)], has_next=True, next_row_key=f"k{i}") for i in range(3)),
            ),
        )
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.gateway"):
            logs = harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0, max_pages=3)
        assert len(harness.log) == 3
        assert len(logs) == 3
        assert caplog.messages == [
            f"getdevicelog for {FAKE_SENSOR_ID} still had rows after 3 pages (3 entries); "
            "the window is not fully covered"
        ]

    def test_one_sensor_sync_pages_the_backfill_then_reads_live_once(self, harness, tmp_db):
        """Invariant 8 with pagination: N log pages, then exactly one live read — one logical sync per sensor."""
        from greenhouse_core.sync import sync_single_sensor

        cluster_id = tmp_db.add_cluster("Paged")
        sensor_id = tmp_db.add_sensor(
            cluster_id=cluster_id, tuya_device_id=FAKE_SENSOR_ID, name="P", sensor_type="tuya.tr301z", config={}
        )
        sensor = tmp_db.get_sensor(sensor_id)
        base_ms = (FROZEN_TS - 3600) * 1000
        harness.cloud.set(
            "getdevicelog",
            Seq(
                _log_page([("humidity", "40", base_ms)], has_next=True, next_row_key="k1"),
                _log_page([("humidity", "41", base_ms + 600_000)], has_next=False),
            ),
        )
        harness.cloud.set(
            "cloudrequest", {"success": True, "result": {"properties": [{"code": "humidity", "value": 42}]}}
        )
        assert sync_single_sensor(tmp_db, harness.gateway, sensor, 24) == (2, 2, 1)
        assert [c[0] for c in harness.log] == ["cloud.getdevicelog", "cloud.getdevicelog", "cloud.cloudrequest"]

    def test_default_page_cap_and_size_come_from_constants(self):
        """The default page size and cap are the documented constants."""
        from greenhouse_core import constants

        assert (constants.SENSOR_LOG_PAGE_SIZE, constants.SENSOR_LOG_MAX_PAGES) == (100, 50)

    @pytest.mark.parametrize("payload", [{}, {"result": {}}, {"success": False}])
    def test_missing_logs_is_empty(self, harness, payload):
        """A payload without logs reads as no logs."""
        harness.cloud.set("getdevicelog", payload)
        assert harness.gateway.get_device_logs(FAKE_SENSOR_ID, since_ms=0) == []

    def test_group_logs_tolerance_boundary_and_timestamp_rules(self):
        """Logs group within the tolerance from the group start, with exact boundary and timestamp rules."""
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
    """Raw Cloud commands and status reads."""

    def test_send_command_success_and_failure(self, harness):
        """``send_command`` reports success and Cloud errors as ``(ok, message)``."""
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
        """``get_status`` returns the Cloud payload unchanged in one call."""
        harness.cloud.set("getstatus", {"anything": [1]})
        assert harness.gateway.get_status(FAKE_DEVICE_ID) == {"anything": [1]}
        assert harness.log == [("cloud.getstatus", FAKE_DEVICE_ID)]


# ── Invariant #8: local key resolution without the Cloud ──────────────────────


class TestLocalKeyResolution:
    """Local-key resolution: config first, then process cache, then one Cloud lookup."""

    @pytest.mark.parametrize(
        "config",
        [
            {"device_ip": FAKE_DEVICE_IP, "local_key": FAKE_LOCAL_KEY},
            f'{{"device_ip": "{FAKE_DEVICE_IP}", "local_key": "{FAKE_LOCAL_KEY}"}}',
        ],
        ids=["dict-config", "json-string-config"],
    )
    def test_open_local_from_config_costs_zero_cloud_calls(self, harness, config):
        """A key in the irrigator config (dict or JSON) opens the device with no Cloud call."""
        harness.gateway.open_local(_irrigator(config), 3.5)
        assert harness.log == _local_open_calls()
        assert harness.cloud_calls() == []

    def test_cold_lookup_once_then_process_cache(self, harness):
        """A missing key is looked up once, reported to the persistence hook, then served from cache."""
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

    def test_resolve_local_key_order(self, harness):
        """Resolution order is config key, then cache, then Cloud; garbage or empty config falls through."""
        gw = harness.gateway
        harness.cloud.set("getdevices", [{"id": FAKE_DEVICE_ID, "key": "from-cloud"}])
        assert gw.resolve_local_key(FAKE_DEVICE_ID, {"local_key": "cfg"}) == "cfg"
        assert gw.resolve_local_key(FAKE_DEVICE_ID, "not json") == "from-cloud"  # garbage config → cold path
        assert gw.resolve_local_key(FAKE_DEVICE_ID, None) == "from-cloud"  # cache hit
        assert gw.resolve_local_key(FAKE_DEVICE_ID, {"local_key": ""}) == "from-cloud"  # empty cfg key → cache
        assert gw.resolve_local_key(FAKE_DEVICE_ID, ["not", "a", "dict"]) == "from-cloud"  # non-dict cfg → cache
        assert harness.log == [("cloud.getdevices",)]

    @pytest.mark.parametrize("devices", [[{"id": "someone-else", "key": "k"}], {"error": "not a list"}, []])
    def test_unknown_device_resolves_none_and_is_not_cached(self, harness, devices):
        """A device the Cloud does not know resolves to None and nothing is cached."""
        harness.cloud.set("getdevices", devices)
        assert harness.gateway.resolve_local_key(FAKE_DEVICE_ID) is None
        assert harness.gateway._key_cache == {}

    def test_persistence_hook_failure_is_logged_not_raised(self, harness, caplog):
        """A failing persistence hook is logged, and the key is still cached and returned."""

        def _boom(device_id, key):
            raise RuntimeError("db locked")

        gateway = DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=harness.cloud, on_key_discovered=_boom)
        harness.cloud.set("getdevices", [{"id": FAKE_DEVICE_ID, "key": "k1"}])
        with caplog.at_level(logging.ERROR, logger="greenhouse_core.devices.gateway"):
            assert gateway.resolve_local_key(FAKE_DEVICE_ID) == "k1"
        assert caplog.messages == [f"local_key persistence hook failed for {FAKE_DEVICE_ID}"]
        assert gateway._key_cache == {FAKE_DEVICE_ID: "k1"}

    def test_open_local_without_ip_never_touches_cloud(self, harness):
        """Without a device IP, opening fails before any call."""
        with pytest.raises(ConnectionError) as excinfo:
            harness.gateway.open_local(_irrigator({"local_key": FAKE_LOCAL_KEY}), 3.5)
        assert str(excinfo.value) == f"No device_ip in config for irrigator {FAKE_DEVICE_ID}."
        assert harness.log == []

    def test_open_local_without_any_key_raises_after_one_lookup(self, harness):
        """With no key anywhere, opening fails after exactly one Cloud lookup."""
        harness.cloud.set("getdevices", [])
        with pytest.raises(ConnectionError) as excinfo:
            harness.gateway.open_local(_irrigator({"device_ip": FAKE_DEVICE_IP}), 3.5)
        assert str(excinfo.value) == f"Could not find local key for device {FAKE_DEVICE_ID}"
        assert harness.log == [("cloud.getdevices",)]


# ── Adapter call sequences (broad golden) ─────────────────────────────────────


def _scenarios():
    """Return the scenario table: name → (setup(harness), action(harness) -> result).

    Keep-alive paths are tested separately.
    """
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
    }


def test_local_status_failure_is_logged_before_cloud_fallback(harness, caplog):
    """The local-read failure behind the Cloud fallback leaves a DEBUG trace (no silent swallow)."""
    harness.local_status_error = OSError("reset by peer")
    harness.cloud.set("getstatus", {"success": True, "result": []})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_core.devices.irrigators.tuya_generic"):
        assert IK10PWAdapter(harness.gateway).status(_irrigator()) == {"source": "cloud"}
    assert [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records] == [
        ("DEBUG", f"Local status read failed for {FAKE_DEVICE_ID}; using Cloud", True)
    ]


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
    """Adapter-level actuation invariants (local DP 102 over v3.5, Cloud switch pulse)."""

    def test_ik10pw_bounded_start_is_local_dp102_over_3_5_then_cloud_switch(self, harness):
        """A bounded start sets Duration DP 102 locally, then pulses the switch via the Cloud."""
        harness.cloud.set("sendcommand", {"success": True})
        result = IK10PWAdapter(harness.gateway).start(_irrigator(), 5)
        assert result == (True, "Irrigation started for 5 min (Duration DP set to 300s, device auto-stops)")
        assert harness.log == [
            *_local_open_calls(),
            ("local.set_value", 102, 300),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
        ]

    def test_ik10pw_local_paths_use_profile_protocol_version(self, harness, monkeypatch):
        """Every local path opens the device with the profile's protocol version (invariant 1)."""
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
        """On / off pulses go through the Cloud only."""
        harness.cloud.set("sendcommand", {"success": True})
        adapter = IK10PWAdapter(harness.gateway)
        adapter.on(_irrigator())
        adapter.off(_irrigator())
        assert harness.log == [
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
            ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_OFF),
        ]

    def test_cloud_switch_transport_error_propagates(self, harness):
        """A transport error on the Cloud switch propagates to the caller."""
        harness.cloud.set("sendcommand", ConnectionError("unreachable"))
        with pytest.raises(ConnectionError):
            IK10PWAdapter(harness.gateway).on(_irrigator())

    @pytest.mark.parametrize(
        ("bitmask", "raw", "tripped"), [(None, 1, True), (2, 1, False), (2, 2, True), (2, 3, True)]
    )
    def test_read_health_uses_profile_bitmask_defaulting_to_bit0(self, harness, bitmask, raw, tripped):
        """The no-water alarm uses the profile's bitmask, bit 0 by default."""
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
        (_reading(water_warning=True), {"alarms": []}),  # B-10: soil-dry alert, not a health alarm
        (_reading(water_warning=False), {"alarms": []}),
        (_reading(water_warning=None), {"alarms": []}),
        (_reading(timestamp=123), {"last_seen_ts": 123}),
    ],
)
def test_tr301z_read_health_derives_from_persisted_row_with_zero_cloud_calls(harness, latest, expected):
    """Sensor health is derived from the persisted reading, with no Cloud call (invariant 8)."""
    state = TR301ZAdapter(harness.gateway).read_health(_sensor(), latest)
    got = _state_dict(state)
    assert got["observed_at"] == FROZEN_TS
    assert {k: got[k] for k in expected} == expected
    assert state.signal_quality is None
    assert state.alarms <= TR301ZAdapter.health_capabilities
    assert harness.log == []


def test_tr301z_read_health_raw_payloads(harness):
    """The health state's raw payload records what it was derived from."""
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


# ── Duration DP unavailable → bounded start is refused (IK10PW, S2 / B-2) ─────


REFUSAL = (
    False,
    "Local Duration DP unavailable (Local connection failed: No device_ip in config for irrigator "
    f"{FAKE_DEVICE_ID}.); keep-alive is not supported in the server — irrigation not started",
)


def _run_in_thread(fn) -> dict:
    outcome: dict = {}

    def _target():
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — the exception is what we assert on
            outcome["exc"] = exc

    worker = threading.Thread(target=_target, name="fastapi-threadpool-stand-in")
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    return outcome


@pytest.mark.parametrize("on_main_thread", [True, False], ids=["main-thread", "worker-thread"])
def test_bounded_start_without_local_duration_is_refused_before_any_on(harness, caplog, on_main_thread):
    """S2 / B-2: no Duration DP → ``(False, …)`` and zero Cloud calls; no keep-alive loop, on any thread."""
    harness.cloud.set("sendcommand", {"success": True})
    adapter = IK10PWAdapter(harness.gateway)
    before = signal.getsignal(signal.SIGTERM)
    with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.irrigators.ik10pw"):
        if on_main_thread:
            result = adapter.start(_irrigator({}), 3)
        else:
            result = _run_in_thread(lambda: adapter.start(_irrigator({}), 3))["result"]
    assert result == REFUSAL
    assert harness.log == []  # no ON, no OFF: the pump was never switched
    assert signal.getsignal(signal.SIGTERM) is before
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def test_local_duration_error_payload_refuses_after_the_local_write_only(harness):
    """A local error payload is a failed Duration write too: only the local calls happen, no Cloud ON."""
    harness.cloud.set("sendcommand", {"success": True})
    harness.local_set_value = {"Error": "Network Error: Device Unreachable", "Err": "905"}
    ok, msg = IK10PWAdapter(harness.gateway).start(_irrigator(), 2)
    assert ok is False
    assert msg.startswith("Local Duration DP unavailable (Local API error: ")
    assert harness.log == [*_local_open_calls(), ("local.set_value", 102, 120)]


def test_keepalive_loop_is_gone():
    """The removed keep-alive loop and its interval constant stay removed."""
    assert not hasattr(IK10PWAdapter, "_start_keepalive")
    assert not hasattr(ik10pw_module, "KEEP_ALIVE_INTERVAL")


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
    """The alarm decoder's type rules: bools ignore the mask, decimal strings count, anything else is False."""
    assert alarm_indicates_no_water(value, bitmask) is expected


@settings(derandomize=True, deadline=None, max_examples=200)
@given(value=st.integers(min_value=-(2**40), max_value=2**40), bitmask=st.integers(min_value=0, max_value=255))
def test_alarm_int_and_decimal_string_agree_with_bitwise_and(value, bitmask):
    """Integer and decimal-string alarm values decode exactly like a bitwise AND."""
    expected = bool(value & bitmask)
    assert alarm_indicates_no_water(value, bitmask) is expected
    assert alarm_indicates_no_water(str(value), bitmask) is expected
