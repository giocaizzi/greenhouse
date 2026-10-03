"""OD3: a row that still carries a legacy device ``type`` is an unknown model.

The registry no longer aliases ``tuya_cloud`` / ``tuya_local`` (irrigators) or
``soil_moisture`` / ``temp_humidity`` / ``light`` (sensors). Pins the failure mode on
every path that resolves an adapter: it is explicit and logged, never a crash, and
the irrigator is never actuated. The fake registry (``tests/server/conftest.py``)
registers only the canonical model keys, exactly like ``build_default_registry``.
"""

from __future__ import annotations

import logging

import pytest

from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.health_monitor import DeviceHealthMonitor

KNOWN_IRRIGATORS = "fake.irrigator, rainpoint.ik10pw"


def _cluster_with_irrigator(client, irrigator_type: str) -> tuple[int, int]:
    cid = client.post("/api/v1/clusters", json={"name": "Legacy", "environment": "indoor"}).json()["id"]
    resp = client.post(
        f"/api/v1/clusters/{cid}/irrigator",
        json={"tuya_device_id": "fake_tuya_device_legacy01", "name": "Old Pump", "type": irrigator_type},
    )
    assert resp.status_code == 201  # the API stores any type string; resolution happens at use
    return cid, resp.json()["id"]


@pytest.mark.parametrize("legacy", ["tuya_cloud", "tuya_local"])
def test_manual_start_refuses_a_legacy_irrigator_with_503(client, app, caplog, legacy):
    _, iid = _cluster_with_irrigator(client, legacy)
    with caplog.at_level(logging.ERROR, logger="greenhouse_core.devices.registry"):
        resp = client.post(f"/api/v1/irrigators/{iid}/start", json={"minutes": 1})
    message = f"No adapter registered for irrigator type '{legacy}' (known: {KNOWN_IRRIGATORS})"
    assert (resp.status_code, resp.json()) == (503, {"detail": message})
    assert caplog.messages == [f"{message}; irrigator {iid} will not be actuated"]
    assert app.state.fake_devices.irrigator.calls == []


def test_forced_cluster_irrigation_skips_actuation_for_a_legacy_irrigator(seeded_client, app):
    client = seeded_client
    assert client.put("/api/v1/clusters/1/irrigator", json={"type": "tuya_cloud"}).status_code == 200
    resp = client.post("/api/v1/clusters/1/irrigate", json={"force": True})
    assert resp.status_code == 200
    assert "no adapter for irrigator: No adapter registered for irrigator type 'tuya_cloud'" in resp.text
    assert app.state.fake_devices.irrigator.calls == []


def test_emergency_stop_reports_a_legacy_irrigator_as_an_error(client, app):
    _, iid = _cluster_with_irrigator(client, "tuya_local")
    resp = client.post("/api/v1/bulk/stop-all", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stopped"] == 0
    assert body["errors"] == [
        f"irrigator {iid} (Old Pump): No adapter registered for irrigator type 'tuya_local' (known: {KNOWN_IRRIGATORS})"
    ]
    assert app.state.fake_devices.irrigator.calls == []


@pytest.mark.parametrize("legacy", ["soil_moisture", "temp_humidity", "light"])
def test_health_poll_skips_a_legacy_sensor_with_a_warning(client, app, caplog, legacy):
    cid = client.post("/api/v1/clusters", json={"name": "Legacy", "environment": "indoor"}).json()["id"]
    sid = client.post(
        f"/api/v1/clusters/{cid}/sensors",
        json={"tuya_device_id": "fake_tuya_sensor_legacy01", "name": "Old Probe", "type": legacy},
    ).json()["id"]
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        monitor = DeviceHealthMonitor(repo, app.state.fake_devices.registry, clock=lambda: 1_700_000_000)
        with caplog.at_level(logging.WARNING, logger="greenhouse_core.devices.registry"):
            state = monitor.poll_sensor(repo.get_sensor(sid))
    finally:
        session.close()
    assert (state.observed_at, state.offline, state.alarms) == (1_700_000_000, False, frozenset())
    assert caplog.messages == [
        f"No adapter registered for sensor type '{legacy}' (known: fake.sensor, tuya.tr301z); "
        f"sensor {sid} will be skipped"
    ]
