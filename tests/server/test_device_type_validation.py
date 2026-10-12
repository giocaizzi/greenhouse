"""An irrigator / sensor ``type`` must be a registered device model key on every write.

The fake registry (``tests/server/conftest.py``) knows ``rainpoint.ik10pw`` + ``fake.irrigator``
and ``tuya.tr301z`` + ``fake.sensor``; without a live registry the canonical keys apply.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.deps import get_device_registry
from greenhouse_server.services.inventory import known_device_types
from server.conftest import app_of

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

IRRIGATOR_422 = "Unknown irrigator type 'acme.pump' (known: rainpoint.ik10pw, fake.irrigator)"
SENSOR_422 = "Unknown sensor type 'acme.probe' (known: tuya.tr301z, fake.sensor)"


def _cluster(client: TestClient) -> int:
    cluster_id: int = client.post("/api/v1/clusters", json={"name": "C"}).json()["id"]
    return cluster_id


def test_api_refuses_an_unknown_irrigator_type_on_create_and_update(client: TestClient) -> None:
    """API refuses an unknown irrigator type on create and update."""
    cid = _cluster(client)
    body = {"tuya_device_id": "fake_tuya_device_aabbccdd", "name": "P", "type": "acme.pump"}
    resp = client.post(f"/api/v1/clusters/{cid}/irrigator", json=body)
    assert (resp.status_code, resp.json()) == (422, {"detail": IRRIGATOR_422})
    assert client.get(f"/api/v1/clusters/{cid}/irrigator").status_code == HTTPStatus.NOT_FOUND  # nothing written
    assert (
        client.post(f"/api/v1/clusters/{cid}/irrigator", json={**body, "type": "rainpoint.ik10pw"}).status_code
        == HTTPStatus.CREATED
    )
    resp = client.put(f"/api/v1/clusters/{cid}/irrigator", json={"type": "acme.pump"})
    assert (resp.status_code, resp.json()) == (422, {"detail": IRRIGATOR_422})
    assert client.get(f"/api/v1/clusters/{cid}/irrigator").json()["type"] == "rainpoint.ik10pw"


def test_api_refuses_an_unknown_sensor_type_on_create_and_update(client: TestClient) -> None:
    """API refuses an unknown sensor type on create and update."""
    cid = _cluster(client)
    body = {"tuya_device_id": "fake_tuya_sensor_00000001", "name": "S", "type": "acme.probe"}
    resp = client.post(f"/api/v1/clusters/{cid}/sensors", json=body)
    assert (resp.status_code, resp.json()) == (422, {"detail": SENSOR_422})
    sid = client.post(f"/api/v1/clusters/{cid}/sensors", json={**body, "type": "tuya.tr301z"}).json()["id"]
    resp = client.put(f"/api/v1/clusters/{cid}/sensors/{sid}", json={"type": "acme.probe"})
    assert (resp.status_code, resp.json()) == (422, {"detail": SENSOR_422})


def test_without_a_live_registry_the_canonical_keys_apply(client: TestClient, app: FastAPI) -> None:
    """Without a live registry the canonical keys apply."""
    app.dependency_overrides[get_device_registry] = lambda: None
    cid = _cluster(client)
    resp = client.post(
        f"/api/v1/clusters/{cid}/irrigator",
        json={"tuya_device_id": "fake_tuya_device_aabbccdd", "name": "P", "type": "fake.irrigator"},
    )
    assert (resp.status_code, resp.json()) == (
        422,
        {"detail": "Unknown irrigator type 'fake.irrigator' (known: rainpoint.ik10pw)"},
    )
    assert known_device_types(None, "sensor") == ("tuya.tr301z",)


def test_web_forms_refuse_an_unknown_type(client: TestClient) -> None:
    """Web forms refuse an unknown type."""
    cid = _cluster(client)
    resp = client.post(
        f"/clusters/{cid}/irrigators",
        data={"tuya_device_id": "fake_tuya_device_aabbccdd", "name": "P", "type": "acme.pump"},
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert IRRIGATOR_422.replace("'", "&#39;") in resp.text  # the add form, re-rendered with the error
    assert '<option value="rainpoint.ik10pw" selected>' in resp.text
    resp = client.post(
        f"/clusters/{cid}/sensors",
        data={"tuya_device_id": "fake_tuya_sensor_00000001", "name": "S", "type": "acme.probe"},
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    sid = client.post(
        f"/api/v1/clusters/{cid}/sensors", json={"tuya_device_id": "s2", "name": "S", "type": "tuya.tr301z"}
    ).json()["id"]
    resp = client.post(f"/clusters/{cid}/sensors/{sid}/edit", data={"name": "S", "type": "acme.probe"})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_edit_form_selects_the_stored_type_and_flags_an_unknown_one(client: TestClient) -> None:
    """Edit form selects the stored type and flags an unknown one."""
    cid = _cluster(client)
    session = app_of(client).state.session_factory()
    repo = IrrigationRepository(session)
    repo.add_irrigator(cluster_id=cid, tuya_device_id="d1", name="P", irrigator_type="fake.irrigator", config={})
    repo.add_sensor(cluster_id=cid, tuya_device_id="s1", name="S", sensor_type="soil_moisture", config={})
    repo.commit()
    sid = repo.get_sensors_in_cluster(cid)[0].id
    session.close()

    page = client.get(f"/clusters/{cid}/irrigators/edit").text
    assert '<option value="fake.irrigator" selected>fake.irrigator</option>' in page
    assert '<option value="rainpoint.ik10pw">Rainpoint IK10PW</option>' in page
    page = client.get(f"/clusters/{cid}/sensors/{sid}/edit").text
    assert '<option value="soil_moisture" selected>soil_moisture (unknown model)</option>' in page


def test_a_legacy_row_stays_editable_while_its_type_is_unchanged(client: TestClient) -> None:
    """Only a new or changed type must be registered: renaming a legacy row is fine (API and web)."""
    cid = _cluster(client)
    session = app_of(client).state.session_factory()
    repo = IrrigationRepository(session)
    repo.add_irrigator(cluster_id=cid, tuya_device_id="d1", name="P", irrigator_type="tuya_cloud", config={})
    sid = repo.add_sensor(cluster_id=cid, tuya_device_id="s1", name="S", sensor_type="soil_moisture", config={})
    repo.commit()
    session.close()

    resp = client.put(f"/api/v1/clusters/{cid}/irrigator", json={"name": "Renamed", "type": "tuya_cloud"})
    assert (resp.status_code, resp.json()["name"]) == (200, "Renamed")
    assert (
        client.put(f"/api/v1/clusters/{cid}/irrigator", json={"type": "tuya_local"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    resp = client.put(f"/api/v1/clusters/{cid}/sensors/{sid}", json={"name": "S2", "type": "soil_moisture"})
    assert (resp.status_code, resp.json()["name"]) == (200, "S2")
    assert (
        client.put(f"/api/v1/clusters/{cid}/sensors/{sid}", json={"type": "light"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    web = client.post(
        f"/clusters/{cid}/irrigators/edit", data={"name": "W", "type": "tuya_cloud"}, follow_redirects=False
    )
    assert web.status_code == HTTPStatus.SEE_OTHER
    web = client.post(f"/clusters/{cid}/sensors/{sid}/edit", data={"name": "W", "type": "soil_moisture"})
    assert web.status_code in (HTTPStatus.OK, HTTPStatus.SEE_OTHER)
    web = client.post(f"/clusters/{cid}/sensors/{sid}/edit", data={"name": "W", "type": "light"})
    assert web.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
