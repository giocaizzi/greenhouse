"""The server persists a ``local_key`` its gateway refreshed, so a re-paired device survives a restart.

``create_app`` wires the gateway's ``on_key_discovered`` hook to
``services.device_keys.local_key_persister``: the key the gateway fetched from the Cloud after a
stale stored key answered error 914 is written to the irrigator's ``config['local_key']`` on its
own session (on a background thread), while API responses keep showing the mask.
"""

from __future__ import annotations

import logging
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Never

import pytest
from fastapi.testclient import TestClient

from devices.test_gateway_key_refresh import FRESH_KEY, HEALTHY_DPS, STALE_KEY, Lan
from fake_data import FAKE_DEVICE_ID, FAKE_DEVICE_IP, FAKE_SENSOR_ID
from greenhouse_core.models import parse_device_config
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.schemas import LOCAL_KEY_MASK
from greenhouse_server import state
from greenhouse_server.services.device_keys import PERSIST_THREAD_NAME, persist_local_key
from server.conftest import _make_stubbed_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    import time_machine
    from fastapi import FastAPI

    from greenhouse_core.models import Irrigator

PERSIST_TIMEOUT_SECONDS = 5.0


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch, frozen_clock: time_machine.Traveller) -> Lan:
    """The fake LAN (a device that answers only to its current key) and its recording Cloud."""
    del frozen_clock  # side-effect fixture (the clock is frozen)
    return Lan(monkeypatch)


@pytest.fixture
def wired_app(fake_tuya_env: None, lan: Lan, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """An app whose real ``DeviceGateway`` (built by ``create_app``) talks to the fake Cloud and LAN."""
    del fake_tuya_env  # side-effect fixture (fake Tuya credentials in the environment)
    monkeypatch.setattr("greenhouse_core.devices.tinytuya.Cloud", lambda **_kwargs: lan.cloud)
    application, engine = _make_stubbed_app(bypass_auth=True)
    yield application
    engine.dispose()


def _seed_irrigator(app: FastAPI, config: dict[str, str]) -> tuple[int, int]:
    """Seed a cluster with one irrigator; return ``(cluster_id, irrigator_id)``."""
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        cluster_id = repo.add_cluster("Key Cluster")
        irrigator_id = repo.add_irrigator(cluster_id, FAKE_DEVICE_ID, "Key Pump", "rainpoint.ik10pw", config)
        repo.commit()
    finally:
        session.close()
    return cluster_id, irrigator_id


def _stored_config(app: FastAPI, irrigator_id: int) -> dict[str, Any]:
    session = app.state.session_factory()
    try:
        irrigator = IrrigationRepository(session).get_irrigator(irrigator_id)
        assert irrigator is not None
        return parse_device_config(irrigator.config)
    finally:
        session.close()


def _run_status(app: FastAPI, irrigator_id: int) -> object:
    gateway = state.device_gateway(app)
    assert gateway is not None
    session = app.state.session_factory()
    try:
        irrigator: Irrigator | None = IrrigationRepository(session).get_irrigator(irrigator_id)
        assert irrigator is not None
        return gateway.run_local(irrigator, 3.5, lambda device: device.status())
    finally:
        session.close()


def _wait_for_persistence() -> None:
    for thread in threading.enumerate():
        if thread.name == PERSIST_THREAD_NAME:
            thread.join(PERSIST_TIMEOUT_SECONDS)
            assert not thread.is_alive()


def test_the_wired_hook_writes_a_refreshed_key_to_the_irrigator_config(wired_app: FastAPI, lan: Lan) -> None:
    """A stale stored key is refreshed, persisted (other config kept) and still masked in the API."""
    lan.cloud_knows(FRESH_KEY)
    cluster_id, irrigator_id = _seed_irrigator(wired_app, {"device_ip": FAKE_DEVICE_IP, "local_key": STALE_KEY})

    assert _run_status(wired_app, irrigator_id) == HEALTHY_DPS
    _wait_for_persistence()

    assert _stored_config(wired_app, irrigator_id) == {"device_ip": FAKE_DEVICE_IP, "local_key": FRESH_KEY}
    response = TestClient(wired_app).get(f"/api/v1/clusters/{cluster_id}/irrigator")
    assert response.status_code == HTTPStatus.OK
    assert response.json()["config"]["local_key"] == LOCAL_KEY_MASK

    lan.log.clear()
    assert _run_status(wired_app, irrigator_id) == HEALTHY_DPS
    assert lan.cloud_calls() == []  # steady state is Cloud-free again


def test_persisting_a_key_no_irrigator_uses_is_a_no_op(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    """A device id that names no irrigator (e.g. a sensor) writes nothing and logs no error."""
    _, irrigator_id = _seed_irrigator(app, {"device_ip": FAKE_DEVICE_IP, "local_key": STALE_KEY})
    with caplog.at_level(logging.DEBUG, logger="greenhouse_server.services.device_keys"):
        persist_local_key(app, FAKE_SENSOR_ID, FRESH_KEY)
    assert _stored_config(app, irrigator_id)["local_key"] == STALE_KEY
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_failed_write_is_logged_and_never_raised(
    app: FastAPI, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write failing rolls back and logs the device id (never the key); nothing propagates."""
    _, irrigator_id = _seed_irrigator(app, {"device_ip": FAKE_DEVICE_IP, "local_key": STALE_KEY})

    def _locked(*_args: object, **_kwargs: object) -> Never:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(IrrigationRepository, "update_irrigator", _locked)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.device_keys"):
        persist_local_key(app, FAKE_DEVICE_ID, FRESH_KEY)

    assert caplog.messages == [f"Could not persist the refreshed local_key for {FAKE_DEVICE_ID}"]
    assert caplog.records[0].exc_info is not None
    assert _stored_config(app, irrigator_id)["local_key"] == STALE_KEY
