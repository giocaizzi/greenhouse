"""CRUD endpoints + engine integration for per-cluster irrigation windows."""

from __future__ import annotations

from http import HTTPStatus


def _make_cluster(client):
    resp = client.post("/api/v1/clusters", json={"name": "Window Cluster"})
    assert resp.status_code == HTTPStatus.CREATED
    return resp.json()["id"]


def test_list_returns_empty_for_new_cluster(client):
    """List returns empty for new cluster."""
    cid = _make_cluster(client)
    resp = client.get(f"/api/v1/clusters/{cid}/windows")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["cluster_id"] == cid
    assert body["windows"] == []


def test_create_window(client):
    """Create window."""
    cid = _make_cluster(client)
    window = {"start_hour": 6, "end_hour": 10, "weekday_mask": 127, "label": "Morning"}
    resp = client.post(f"/api/v1/clusters/{cid}/windows", json=window)
    assert resp.status_code == HTTPStatus.CREATED
    body = resp.json()
    assert body["cluster_id"] == cid
    assert {key: body[key] for key in window} == window


def test_update_window(client):
    """Update window."""
    cid = _make_cluster(client)
    original = {"start_hour": 6, "end_hour": 10}
    update = {"end_hour": 11, "label": "Extended Morning"}
    create = client.post(f"/api/v1/clusters/{cid}/windows", json=original)
    wid = create.json()["id"]
    resp = client.put(f"/api/v1/clusters/{cid}/windows/{wid}", json=update)
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["end_hour"] == update["end_hour"]
    assert body["start_hour"] == original["start_hour"]  # untouched
    assert body["label"] == update["label"]


def test_delete_window(client):
    """Delete window."""
    cid = _make_cluster(client)
    create = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 6, "end_hour": 10})
    wid = create.json()["id"]
    resp = client.delete(f"/api/v1/clusters/{cid}/windows/{wid}")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["success"] is True
    listed = client.get(f"/api/v1/clusters/{cid}/windows").json()["windows"]
    assert listed == []


def test_create_rejects_invalid_hours(client):
    """Create rejects invalid hours."""
    cid = _make_cluster(client)
    resp = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 25, "end_hour": 5})
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    resp = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 6, "end_hour": 6})
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_create_rejects_invalid_mask(client):
    """Create rejects invalid mask."""
    cid = _make_cluster(client)
    resp = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10, "weekday_mask": 0},
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    resp = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10, "weekday_mask": 200},
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_window_404_on_other_cluster(client):
    """Window 404 on other cluster."""
    a = _make_cluster(client)
    b = _make_cluster(client)
    create = client.post(f"/api/v1/clusters/{a}/windows", json={"start_hour": 6, "end_hour": 10})
    wid = create.json()["id"]
    # Window belongs to cluster A; cluster B must not see it.
    resp = client.put(f"/api/v1/clusters/{b}/windows/{wid}", json={"end_hour": 11})
    assert resp.status_code == HTTPStatus.NOT_FOUND
    resp = client.delete(f"/api/v1/clusters/{b}/windows/{wid}")
    assert resp.status_code == HTTPStatus.NOT_FOUND
