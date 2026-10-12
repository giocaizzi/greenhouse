"""A stale ``local_key`` (the device was re-paired) is refreshed once from the Cloud and retried.

The fake LAN below answers like tinytuya: a device opened with a key it no longer holds
returns the error dict ``{"Error": "Check device key or version", "Err": "914", ...}``
instead of raising. The gateway's :meth:`DeviceGateway.run_local` then makes ONE Cloud
``getdevices`` lookup (rate-limited per device) and, only when the Cloud key differs, retries
the local call once with it. Steady state stays Cloud-free (invariant 8), and the Cloud switch
ON of a bounded IK10PW start is still sent once, after the Duration DP write succeeded.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Never

import pytest

from devices.test_contract_adapters import SWITCH_ON, RecordingCloud, _as_cloud, _irrigator, _local_open_calls
from fake_data import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_DEVICE_ID,
    FAKE_DEVICE_ID_2,
    FAKE_DEVICE_IP,
    FAKE_LOCAL_KEY,
    FAKE_LOCAL_KEY_REPAIRED,
)
from greenhouse_core.constants import LOCAL_KEY_REFRESH_INTERVAL_SECONDS
from greenhouse_core.devices import DeviceGateway, IK10PWAdapter

if TYPE_CHECKING:
    from collections.abc import Callable

    import time_machine

    from devices.test_contract_adapters import LogEntry
    from greenhouse_core.models import Irrigator

STALE_KEY = FAKE_LOCAL_KEY
FRESH_KEY = FAKE_LOCAL_KEY_REPAIRED
KEY_ERROR = {"Error": "Check device key or version", "Err": "914", "Payload": None}
OFFLINE_ERROR = {"Error": "Network Error: Device Unreachable", "Err": "905", "Payload": None}
HEALTHY_DPS = {"dps": {"1": False, "104": 0, "105": 0, "106": 0}}
KEYS_LOGGER = "greenhouse_core.devices.local_keys"


class Lan:
    """A recording Cloud plus a fake LAN whose device answers only to its current key."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch tinytuya's ``OutletDevice`` and build a gateway with a recording persistence hook."""
        self.log: list[LogEntry] = []
        self.cloud = RecordingCloud(self.log)
        self.device_keys: dict[str, str] = {FAKE_DEVICE_ID: FRESH_KEY, FAKE_DEVICE_ID_2: FRESH_KEY}
        self.discovered: list[tuple[str, str]] = []
        lan = self

        class KeyAwareOutletDevice:
            def __init__(self, dev_id: str, address: str, local_key: str) -> None:
                self.dev_id, self.local_key = dev_id, local_key
                lan.log.append(("local.OutletDevice", dev_id, address, local_key))

            def set_version(self, version: float) -> None:
                lan.log.append(("local.set_version", version))

            def set_socketTimeout(self, seconds: float) -> None:  # noqa: N802 — tinytuya's name
                lan.log.append(("local.set_socketTimeout", seconds))

            def _answer(self, ok: object) -> object:
                return ok if lan.device_keys[self.dev_id] == self.local_key else dict(KEY_ERROR)

            def set_value(self, dp: int | str, value: object) -> object:
                lan.log.append(("local.set_value", dp, value))
                return self._answer(None)

            def status(self) -> object:
                lan.log.append(("local.status",))
                return self._answer(HEALTHY_DPS)

        monkeypatch.setattr("greenhouse_core.devices.tinytuya.OutletDevice", KeyAwareOutletDevice)
        self.gateway = self.build_gateway(lambda device_id, key: self.discovered.append((device_id, key)))

    def build_gateway(self, hook: Callable[[str, str], None]) -> DeviceGateway:
        """A gateway over this LAN's recording Cloud with ``hook`` as its persistence hook."""
        return DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=_as_cloud(self.cloud), on_key_discovered=hook)

    def lookups(self) -> int:
        """How many Cloud ``getdevices`` key lookups were made."""
        return self.log.count(("cloud.getdevices",))

    def cloud_calls(self) -> list[LogEntry]:
        """Only the Cloud calls from the shared log."""
        return [c for c in self.log if str(c[0]).startswith("cloud.")]

    def cloud_knows(self, key: str, device_id: str = FAKE_DEVICE_ID) -> None:
        """Script the Cloud ``getdevices`` listing to hold ``key`` for ``device_id``."""
        self.cloud.set("getdevices", [{"id": "someone-else", "key": "nope"}, {"id": device_id, "key": key}])


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch, frozen_clock: time_machine.Traveller) -> Lan:
    """A fake LAN with the clock frozen at ``FROZEN_TS``."""
    del frozen_clock  # side-effect fixture (the clock is frozen)
    return Lan(monkeypatch)


def _stale(device_id: str = FAKE_DEVICE_ID) -> Irrigator:
    irrigator = _irrigator({"device_ip": FAKE_DEVICE_IP, "local_key": STALE_KEY})
    irrigator.tuya_device_id = device_id
    return irrigator


def _status(device: Any) -> object:  # noqa: ANN401 — the tinytuya device is untyped
    return device.status()


def _opened(key: str) -> list[LogEntry]:
    return _local_open_calls(key)


# ── Gateway: refresh, retry, rate limit ────────────────────────────────────────


def test_stale_config_key_is_refreshed_once_and_the_new_key_is_used_thereafter(lan: Lan) -> None:
    """914 with the stored key → one Cloud lookup → one retry with the new key; later calls skip the Cloud."""
    lan.cloud_knows(FRESH_KEY)
    irrigator = _stale()

    assert lan.gateway.run_local(irrigator, 3.5, _status) == HEALTHY_DPS
    assert lan.log == [
        *_opened(STALE_KEY),
        ("local.status",),
        ("cloud.getdevices",),
        *_opened(FRESH_KEY),
        ("local.status",),
    ]
    assert lan.discovered == [(FAKE_DEVICE_ID, FRESH_KEY)]

    lan.log.clear()
    assert lan.gateway.run_local(irrigator, 3.5, _status) == HEALTHY_DPS  # config still holds the stale key
    assert lan.log == [*_opened(FRESH_KEY), ("local.status",)]
    assert lan.gateway.resolve_local_key(FAKE_DEVICE_ID, irrigator.config) == FRESH_KEY


def test_same_key_from_the_cloud_keeps_the_original_failure_without_a_retry(lan: Lan) -> None:
    """When the Cloud holds the key that failed, 914 is not a stale key: no retry, the error stands."""
    lan.device_keys[FAKE_DEVICE_ID] = "mis-versioned!!!"  # nothing the Cloud knows opens it
    lan.cloud_knows(STALE_KEY)

    assert lan.gateway.run_local(_stale(), 3.5, _status) == KEY_ERROR
    assert lan.log == [*_opened(STALE_KEY), ("local.status",), ("cloud.getdevices",)]
    assert lan.discovered == []


def test_the_key_refresh_is_rate_limited_per_device(lan: Lan, frozen_clock: time_machine.Traveller) -> None:
    """At most one Cloud lookup per device per interval; another device has its own budget."""
    lan.device_keys[FAKE_DEVICE_ID] = lan.device_keys[FAKE_DEVICE_ID_2] = "mis-versioned!!!"
    lan.cloud_knows(STALE_KEY)
    for _ in range(3):
        assert lan.gateway.run_local(_stale(), 3.5, _status) == KEY_ERROR
    assert lan.lookups() == 1

    frozen_clock.shift(LOCAL_KEY_REFRESH_INTERVAL_SECONDS - 1)
    lan.gateway.run_local(_stale(), 3.5, _status)
    assert lan.lookups() == 1

    lan.gateway.run_local(_stale(FAKE_DEVICE_ID_2), 3.5, _status)
    assert lan.lookups() == 2  # noqa: PLR2004 — the second device's own lookup

    frozen_clock.shift(1)
    lan.gateway.run_local(_stale(), 3.5, _status)
    assert lan.lookups() == 3  # noqa: PLR2004 — the first device's budget is back after one interval


@pytest.mark.parametrize(
    "answer",
    [RuntimeError("cloud down"), {"success": False, "msg": "sign invalid"}, [{"id": "someone-else", "key": "k"}]],
    ids=["raises", "error-payload", "device-missing"],
)
def test_a_failed_cloud_lookup_keeps_the_original_failure(lan: Lan, answer: object) -> None:
    """A lookup that raises, errors or does not list the device leaves the 914 result as it was."""
    lan.cloud.set("getdevices", answer)

    assert lan.gateway.run_local(_stale(), 3.5, _status) == KEY_ERROR
    assert lan.log == [*_opened(STALE_KEY), ("local.status",), ("cloud.getdevices",)]
    assert lan.discovered == []


def test_other_local_failures_never_touch_the_cloud(lan: Lan) -> None:
    """Only error 914 refreshes the key: another error dict or a raised error goes straight back."""

    def unreachable(device: Any) -> object:  # noqa: ANN401 — the tinytuya device is untyped
        del device
        return dict(OFFLINE_ERROR)

    def broken(device: Any) -> Never:  # noqa: ANN401 — the tinytuya device is untyped
        del device
        msg = "no route to host"
        raise ConnectionError(msg)

    assert lan.gateway.run_local(_stale(), 3.5, unreachable) == OFFLINE_ERROR
    with pytest.raises(ConnectionError, match="no route to host"):
        lan.gateway.run_local(_stale(), 3.5, broken)
    assert lan.cloud_calls() == []


def test_a_failing_persistence_hook_neither_breaks_the_retry_nor_the_override(
    lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """The refreshed key is retried and kept in-process even when persisting it fails (logged)."""

    def _boom(_device_id: str, _key: str) -> Never:
        msg = "database is locked"
        raise RuntimeError(msg)

    gateway = lan.build_gateway(_boom)
    lan.cloud_knows(FRESH_KEY)
    with caplog.at_level(logging.ERROR, logger=KEYS_LOGGER):
        assert gateway.run_local(_stale(), 3.5, _status) == HEALTHY_DPS
    assert caplog.messages == [f"local_key persistence hook failed for {FAKE_DEVICE_ID}"]
    assert gateway.resolve_local_key(FAKE_DEVICE_ID, {"local_key": STALE_KEY}) == FRESH_KEY


def test_a_new_config_key_still_wins_over_the_refreshed_one(lan: Lan) -> None:
    """Only the superseded key is overridden: an operator-entered new key in the config is used as is."""
    lan.cloud_knows(FRESH_KEY)
    lan.gateway.run_local(_stale(), 3.5, _status)

    assert lan.gateway.resolve_local_key(FAKE_DEVICE_ID, {"local_key": "operator-new-key"}) == "operator-new-key"
    assert lan.gateway.resolve_local_key(FAKE_DEVICE_ID, {"local_key": FRESH_KEY}) == FRESH_KEY


def test_the_refresh_logs_one_warning_then_info_and_never_the_key(lan: Lan, caplog: pytest.LogCaptureFixture) -> None:
    """A WARNING names the 914 refresh, an INFO the new key; neither key value is ever logged."""
    lan.cloud_knows(FRESH_KEY)
    with caplog.at_level(logging.DEBUG, logger=KEYS_LOGGER):
        lan.gateway.run_local(_stale(), 3.5, _status)

    levels = [r.levelno for r in caplog.records if r.name == KEYS_LOGGER]
    assert levels == [logging.WARNING, logging.INFO]
    assert all(FAKE_DEVICE_ID in m for m in caplog.messages)
    assert not any(STALE_KEY in m or FRESH_KEY in m for m in caplog.messages)


# ── IK10PW: the three local call sites recover ────────────────────────────────


def test_ik10pw_bounded_start_with_a_stale_key_succeeds_with_exactly_one_on(lan: Lan) -> None:
    """The Duration DP write is retried with the refreshed key; the Cloud ON follows once."""
    lan.cloud_knows(FRESH_KEY)
    lan.cloud.set("sendcommand", {"success": True})

    result = IK10PWAdapter(lan.gateway).start(_stale(), 5)

    assert result == (True, "Irrigation started for 5 min (Duration DP set to 300s, device auto-stops)")
    assert lan.log == [
        *_opened(STALE_KEY),
        ("local.set_value", 102, 300),
        ("cloud.getdevices",),
        *_opened(FRESH_KEY),
        ("local.set_value", 102, 300),
        ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON),
    ]


def test_ik10pw_bounded_start_is_still_refused_when_the_cloud_key_is_unchanged(lan: Lan) -> None:
    """No new key → the Duration write stays failed and no ON is sent at all."""
    lan.device_keys[FAKE_DEVICE_ID] = "mis-versioned!!!"
    lan.cloud_knows(STALE_KEY)

    ok, message = IK10PWAdapter(lan.gateway).start(_stale(), 5)

    assert not ok
    assert "irrigation not started" in message
    assert ("cloud.sendcommand", FAKE_DEVICE_ID, SWITCH_ON) not in lan.log


def test_ik10pw_health_and_status_reads_recover_from_a_stale_key(lan: Lan) -> None:
    """The DP 105 health read and the local status read both retry with the refreshed key."""
    lan.cloud_knows(FRESH_KEY)
    adapter = IK10PWAdapter(lan.gateway)

    health = adapter.read_health(_stale())
    status = adapter.status(_stale())

    assert (health.offline, health.alarms, health.raw["source"]) == (False, frozenset(), "local")
    assert status["source"] == "local"
    assert lan.lookups() == 1
    assert lan.log[-len(_opened(FRESH_KEY)) - 1 :] == [*_opened(FRESH_KEY), ("local.status",)]
