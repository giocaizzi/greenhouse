"""Measurements and thresholds refuse ``NaN`` / ``Infinity`` in API bodies (422) and web forms (400)."""

from __future__ import annotations

from http import HTTPStatus

import pytest


@pytest.fixture
def cid(client) -> int:
    return client.post("/api/v1/clusters", json={"name": "C"}).json()["id"]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_api_bodies_refuse_non_finite_numbers(client, cid, bad):
    """API bodies refuse non finite numbers."""

    def put(method, url, body):
        return client.request(method, url, content=body, headers={"Content-Type": "application/json"})

    assert (
        put("POST", f"/api/v1/clusters/{cid}/plants", f'{{"species": "Fern", "ideal_temp_min": {bad}}}').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Fern"}).json()["id"]
    assert (
        put("PUT", f"/api/v1/clusters/{cid}/plants/{pid}", f'{{"ideal_humidity_max": {bad}}}').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    body = f'{{"tuya_device_id": "d1", "name": "P", "type": "rainpoint.ik10pw", "reservoir_l": {bad}}}'
    assert put("POST", f"/api/v1/clusters/{cid}/irrigator", body).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert (
        client.post(
            f"/api/v1/clusters/{cid}/irrigator", json={"tuya_device_id": "d1", "name": "P", "type": "rainpoint.ik10pw"}
        ).status_code
        == HTTPStatus.CREATED
    )
    assert (
        put("PUT", f"/api/v1/clusters/{cid}/irrigator", f'{{"flow_rate_l_per_min": {bad}}}').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        put("POST", f"/api/v1/clusters/{cid}/irrigate", f'{{"dry_run": true, "temp_override": {bad}}}').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_web_forms_refuse_non_finite_numbers(client, cid, bad):
    """Web forms refuse non finite numbers."""
    html = {"Accept": "text/html"}
    resp = client.post(f"/clusters/{cid}/plants", data={"species": "Fern", "ideal_temp_min": bad}, headers=html)
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    resp = client.post(
        f"/clusters/{cid}/irrigators",
        data={"tuya_device_id": "d1", "name": "P", "type": "rainpoint.ik10pw", "reservoir_l": bad},
        headers=html,
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    resp = client.post(f"/clusters/{cid}/irrigate", data={"dry_run": "1", "temp_override": bad}, headers=html)
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert client.get(f"/api/v1/clusters/{cid}/plants").json() == []
