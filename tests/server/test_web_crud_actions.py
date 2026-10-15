"""Web edit/delete pages for cluster, plant, sensor, irrigator."""

from http import HTTPStatus

from fastapi.testclient import TestClient

# --- Cluster edit/delete --------------------------------------------------


def test_cluster_edit_form_renders(seeded_client: TestClient) -> None:
    """Cluster edit form renders."""
    resp = seeded_client.get("/clusters/1/edit")
    assert resp.status_code == HTTPStatus.OK
    assert 'value="Test Cluster"' in resp.text
    assert 'name="environment"' in resp.text


def test_cluster_edit_post_updates(seeded_client: TestClient) -> None:
    """Cluster edit post updates."""
    resp = seeded_client.post(
        "/clusters/1/edit",
        data={"name": "Renamed Cluster", "location": "Loft", "environment": "indoor"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    resp2 = seeded_client.get("/clusters")
    assert "Renamed Cluster" in resp2.text
    assert "Loft" in resp2.text


def test_cluster_delete_removes_row(seeded_client: TestClient) -> None:
    """Cluster delete removes row."""
    resp = seeded_client.delete("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert resp.text == ""
    resp2 = seeded_client.get("/clusters")
    assert "Test Cluster" not in resp2.text


def test_cluster_delete_404(client: TestClient) -> None:
    """Cluster delete 404."""
    resp = client.delete("/clusters/9999")
    assert resp.status_code == HTTPStatus.NOT_FOUND


# --- Plant edit/delete ----------------------------------------------------


def test_plant_edit_form_renders(seeded_client: TestClient) -> None:
    """Plant edit form renders."""
    resp = seeded_client.get("/clusters/1/plants/1/edit")
    assert resp.status_code == HTTPStatus.OK
    assert 'value="Monstera deliciosa"' in resp.text


def test_plant_edit_updates(seeded_client: TestClient) -> None:
    """Plant edit updates."""
    resp = seeded_client.post(
        "/clusters/1/plants/1/edit",
        data={"species": "Monstera adansonii", "water_needs": "high"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    resp2 = seeded_client.get("/clusters/1/plants")
    assert "Monstera adansonii" in resp2.text


def test_plant_delete_removes_row(seeded_client: TestClient) -> None:
    """Plant delete archives it: gone from the plants table, listed in the Archived card instead."""
    resp = seeded_client.delete("/clusters/1/plants/1")
    assert resp.status_code == HTTPStatus.OK
    resp2 = seeded_client.get("/clusters/1/plants")
    assert 'id="plant-row-1"' not in resp2.text
    assert 'id="archived-plant-row-1"' in resp2.text


def test_plant_edit_404_wrong_cluster(seeded_client: TestClient) -> None:
    """Plant edit 404 wrong cluster."""
    seeded_client.post("/api/v1/clusters", json={"name": "Other"})
    resp = seeded_client.get("/clusters/2/plants/1/edit")
    assert resp.status_code == HTTPStatus.NOT_FOUND


# --- Sensor edit/delete ---------------------------------------------------


def test_sensor_edit_form_renders(seeded_client: TestClient) -> None:
    """Sensor edit form renders."""
    resp = seeded_client.get("/clusters/1/sensors/1/edit")
    assert resp.status_code == HTTPStatus.OK
    assert 'value="Test Sensor"' in resp.text
    # plant options visible
    assert "Monstera deliciosa" in resp.text


def test_sensor_edit_updates(seeded_client: TestClient) -> None:
    """Sensor edit updates."""
    resp = seeded_client.post(
        "/clusters/1/sensors/1/edit",
        data={"name": "Renamed Sensor", "type": "tuya.tr301z", "plant_id": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    resp2 = seeded_client.get("/clusters/1/sensors")
    assert "Renamed Sensor" in resp2.text


def test_sensor_delete_removes_row(seeded_client: TestClient) -> None:
    """Sensor delete archives it: gone from the sensors table, listed in the Archived card instead."""
    resp = seeded_client.delete("/clusters/1/sensors/1")
    assert resp.status_code == HTTPStatus.OK
    resp2 = seeded_client.get("/clusters/1/sensors")
    assert 'id="sensor-row-1"' not in resp2.text
    assert 'id="archived-sensor-row-1"' in resp2.text


# --- Irrigator edit/delete ------------------------------------------------


def test_irrigator_list_now_shows_controls_and_edit_delete(seeded_client: TestClient) -> None:
    """Irrigator list now shows controls and edit delete."""
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    # start/stop controls reused via partial
    assert 'hx-post="/irrigators/1/start"' in resp.text
    assert 'hx-post="/irrigators/1/stop"' in resp.text
    # edit + delete (cluster-keyed singular paths)
    assert 'href="/clusters/1/irrigators/edit"' in resp.text
    assert 'hx-delete="/clusters/1/irrigators"' in resp.text


def test_irrigator_edit_form_renders(seeded_client: TestClient) -> None:
    """Irrigator edit form renders."""
    resp = seeded_client.get("/clusters/1/irrigators/edit")
    assert resp.status_code == HTTPStatus.OK
    assert 'value="Test Irrigator"' in resp.text


def test_irrigator_edit_updates(seeded_client: TestClient) -> None:
    """Irrigator edit updates."""
    resp = seeded_client.post(
        "/clusters/1/irrigators/edit",
        data={"name": "Pump X", "type": "rainpoint.ik10pw"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    resp2 = seeded_client.get("/clusters/1")
    assert "Pump X" in resp2.text


def test_irrigator_delete_removes_row(seeded_client: TestClient) -> None:
    """Irrigator delete removes row."""
    resp = seeded_client.delete("/clusters/1/irrigators")
    assert resp.status_code == HTTPStatus.OK
    resp2 = seeded_client.get("/clusters/1")
    assert "Test Irrigator" not in resp2.text
