"""CRUD endpoints + engine integration for per-cluster irrigation windows."""

from __future__ import annotations


def _make_cluster(client):
    resp = client.post("/api/v1/clusters", json={"name": "Window Cluster"})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_list_returns_empty_for_new_cluster(client):
    cid = _make_cluster(client)
    resp = client.get(f"/api/v1/clusters/{cid}/windows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_id"] == cid
    assert body["windows"] == []


def test_create_window(client):
    cid = _make_cluster(client)
    resp = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10, "weekday_mask": 127, "label": "Morning"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cluster_id"] == cid
    assert body["start_hour"] == 6
    assert body["end_hour"] == 10
    assert body["weekday_mask"] == 127
    assert body["label"] == "Morning"


def test_update_window(client):
    cid = _make_cluster(client)
    create = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10},
    )
    wid = create.json()["id"]
    resp = client.put(
        f"/api/v1/clusters/{cid}/windows/{wid}",
        json={"end_hour": 11, "label": "Extended Morning"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["end_hour"] == 11
    assert body["start_hour"] == 6  # untouched
    assert body["label"] == "Extended Morning"


def test_delete_window(client):
    cid = _make_cluster(client)
    create = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 6, "end_hour": 10})
    wid = create.json()["id"]
    resp = client.delete(f"/api/v1/clusters/{cid}/windows/{wid}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    listed = client.get(f"/api/v1/clusters/{cid}/windows").json()["windows"]
    assert listed == []


def test_create_rejects_invalid_hours(client):
    cid = _make_cluster(client)
    resp = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 25, "end_hour": 5})
    assert resp.status_code == 400
    resp = client.post(f"/api/v1/clusters/{cid}/windows", json={"start_hour": 6, "end_hour": 6})
    assert resp.status_code == 400


def test_create_rejects_invalid_mask(client):
    cid = _make_cluster(client)
    resp = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10, "weekday_mask": 0},
    )
    assert resp.status_code == 400
    resp = client.post(
        f"/api/v1/clusters/{cid}/windows",
        json={"start_hour": 6, "end_hour": 10, "weekday_mask": 200},
    )
    assert resp.status_code == 400


def test_window_404_on_other_cluster(client):
    a = _make_cluster(client)
    b = _make_cluster(client)
    create = client.post(f"/api/v1/clusters/{a}/windows", json={"start_hour": 6, "end_hour": 10})
    wid = create.json()["id"]
    # Window belongs to cluster A; cluster B must not see it.
    resp = client.put(f"/api/v1/clusters/{b}/windows/{wid}", json={"end_hour": 11})
    assert resp.status_code == 404
    resp = client.delete(f"/api/v1/clusters/{b}/windows/{wid}")
    assert resp.status_code == 404
