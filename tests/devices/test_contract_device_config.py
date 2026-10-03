"""Characterization: how the DeviceGateway reads an irrigator's stored ``config`` blob.

Pins the lenient parse behind ``resolve_local_key`` / ``open_local`` for every input
shape a DB row or caller can hand it — dict, JSON object text, non-object JSON,
malformed text, non-string values — so swapping the gateway's private parser for
``models.parse_device_config`` (drift D16b) is provably behavior-identical.
"""

from __future__ import annotations

import pytest

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_DEVICE_ID, FAKE_DEVICE_IP
from greenhouse_core.devices import DeviceGateway
from greenhouse_core.models import Irrigator, parse_device_config


class _KeyCloud:
    """Cloud double: only ``getdevices`` is allowed and it is counted."""

    def __init__(self) -> None:
        self.calls = 0

    def getdevices(self) -> list[dict[str, str]]:
        self.calls += 1
        return [{"id": FAKE_DEVICE_ID, "key": "from-cloud"}]


class _StrSub(str):
    """A ``str`` subclass (still JSON text to both parsers)."""


# (config input, expected parsed dict) — the config-borne key is "cfg" whenever the parse yields one.
CASES = [
    ({"local_key": "cfg"}, {"local_key": "cfg"}),
    ('{"local_key": "cfg"}', {"local_key": "cfg"}),
    ('  {"local_key": "cfg"}\n', {"local_key": "cfg"}),
    (_StrSub('{"local_key": "cfg"}'), {"local_key": "cfg"}),
    ('{"device_ip": "x"}', {"device_ip": "x"}),
    ("{}", {}),
    ("null", {}),
    ("[1, 2]", {}),
    ('"local_key"', {}),
    ("123", {}),
    ("true", {}),
    ("", {}),
    ("{not json", {}),
    ('{"local_key": "cfg"} trailing', {}),
    (b'{"local_key": "cfg"}', {}),
    (bytearray(b"{}"), {}),
    (5, {}),
    (1.5, {}),
    (["local_key", "cfg"], {}),
    (("local_key",), {}),
    (object(), {}),
]
IDS = [
    "dict",
    "json-object",
    "json-object-padded",
    "str-subclass",
    "json-object-no-key",
    "json-empty-object",
    "json-null",
    "json-array",
    "json-string",
    "json-number",
    "json-bool",
    "empty-string",
    "malformed",
    "trailing-garbage",
    "bytes",
    "bytearray",
    "int",
    "float",
    "list",
    "tuple",
    "object",
]


def _gateway() -> tuple[DeviceGateway, _KeyCloud]:
    cloud = _KeyCloud()
    return DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "eu", raw=cloud), cloud


@pytest.mark.parametrize(("config", "parsed"), CASES, ids=IDS)
def test_resolve_local_key_reads_config_leniently(config: object, parsed: dict[str, str]) -> None:
    gateway, cloud = _gateway()
    key = gateway.resolve_local_key(FAKE_DEVICE_ID, config)
    if parsed.get("local_key"):
        assert (key, cloud.calls) == ("cfg", 0)
    else:
        assert (key, cloud.calls) == ("from-cloud", 1)


@pytest.mark.parametrize(("config", "parsed"), CASES, ids=IDS)
def test_parse_device_config_matches_the_gateway_reading(config: object, parsed: dict[str, str]) -> None:
    assert parse_device_config(config) == parsed


def test_parse_device_config_returns_a_dict_input_as_is() -> None:
    config = {"local_key": "cfg"}
    assert parse_device_config(config) is config


@pytest.mark.parametrize("config", ["null", "[1]", '"192.0.2.1"', "{bad", 7, None], ids=str)
def test_open_local_without_object_config_reports_missing_ip(config: object) -> None:
    gateway, cloud = _gateway()
    irrigator = Irrigator(id=7, cluster_id=1, tuya_device_id=FAKE_DEVICE_ID, name="P", type="rainpoint.ik10pw")
    irrigator.config = config  # type: ignore[assignment]  # stored rows may hold any of these
    with pytest.raises(ConnectionError, match=f"^No device_ip in config for irrigator {FAKE_DEVICE_ID}\\.$"):
        gateway.open_local(irrigator, 3.5)
    assert cloud.calls == 0


def test_open_local_reads_ip_from_json_text(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, str, str]] = []

    class _Outlet:
        def __init__(self, dev_id: str, address: str, local_key: str) -> None:
            opened.append((dev_id, address, local_key))

        def set_version(self, version: float) -> None:
            pass

        def set_socketTimeout(self, seconds: float) -> None:  # noqa: N802 — tinytuya's name
            pass

    monkeypatch.setattr("greenhouse_core.devices.tinytuya.OutletDevice", _Outlet)
    gateway, cloud = _gateway()
    irrigator = Irrigator(
        id=7,
        cluster_id=1,
        tuya_device_id=FAKE_DEVICE_ID,
        name="P",
        type="rainpoint.ik10pw",
        config=f'{{"device_ip": "{FAKE_DEVICE_IP}"}}',
    )
    gateway.open_local(irrigator, 3.5)
    assert opened == [(FAKE_DEVICE_ID, FAKE_DEVICE_IP, "from-cloud")]
    assert cloud.calls == 1
