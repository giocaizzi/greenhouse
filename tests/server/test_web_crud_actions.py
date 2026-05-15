"""Web edit/delete pages for cluster, plant, sensor, irrigator."""


# --- Cluster edit/delete --------------------------------------------------


def test_cluster_list_shows_edit_and_delete(seeded_client):
    resp = seeded_client.get("/clusters")
    assert resp.status_code == 200
    assert 'href="/clusters/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1"' in resp.text


def test_cluster_edit_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/edit")
    assert resp.status_code == 200
    assert 'value="Test Cluster"' in resp.text
    assert 'name="environment"' in resp.text


def test_cluster_edit_post_updates(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/edit",
        data={"name": "Renamed Cluster", "location": "Loft", "environment": "indoor"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp2 = seeded_client.get("/clusters")
    assert "Renamed Cluster" in resp2.text
    assert "Loft" in resp2.text


def test_cluster_delete_removes_row(seeded_client):
    resp = seeded_client.delete("/clusters/1")
    assert resp.status_code == 200
    assert resp.text == ""
    resp2 = seeded_client.get("/clusters")
    assert "Test Cluster" not in resp2.text


def test_cluster_delete_404(client):
    resp = client.delete("/clusters/9999")
    assert resp.status_code == 404


# --- Plant edit/delete ----------------------------------------------------


def test_plant_list_shows_edit_and_delete(seeded_client):
    resp = seeded_client.get("/clusters/1/plants")
    assert resp.status_code == 200
    assert 'href="/clusters/1/plants/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1/plants/1"' in resp.text


def test_plant_edit_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1/edit")
    assert resp.status_code == 200
    assert 'value="Monstera deliciosa"' in resp.text


def test_plant_edit_updates(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/plants/1/edit",
        data={"species": "Monstera adansonii", "water_needs": "high"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp2 = seeded_client.get("/clusters/1/plants")
    assert "Monstera adansonii" in resp2.text


def test_plant_delete_removes_row(seeded_client):
    resp = seeded_client.delete("/clusters/1/plants/1")
    assert resp.status_code == 200
    resp2 = seeded_client.get("/clusters/1/plants")
    assert "Monstera deliciosa" not in resp2.text


def test_plant_edit_404_wrong_cluster(seeded_client):
    seeded_client.post("/api/v1/clusters", json={"name": "Other"})
    resp = seeded_client.get("/clusters/2/plants/1/edit")
    assert resp.status_code == 404


# --- Sensor edit/delete ---------------------------------------------------


def test_sensor_list_shows_edit_and_delete(seeded_client):
    resp = seeded_client.get("/clusters/1/sensors")
    assert resp.status_code == 200
    assert 'href="/clusters/1/sensors/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1/sensors/1"' in resp.text


def test_sensor_edit_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/sensors/1/edit")
    assert resp.status_code == 200
    assert 'value="Test Sensor"' in resp.text
    # plant options visible
    assert "Monstera deliciosa" in resp.text


def test_sensor_edit_updates(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/sensors/1/edit",
        data={"name": "Renamed Sensor", "type": "soil_moisture", "plant_id": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp2 = seeded_client.get("/clusters/1/sensors")
    assert "Renamed Sensor" in resp2.text


def test_sensor_delete_removes_row(seeded_client):
    resp = seeded_client.delete("/clusters/1/sensors/1")
    assert resp.status_code == 200
    resp2 = seeded_client.get("/clusters/1/sensors")
    assert "Test Sensor" not in resp2.text


# --- Irrigator edit/delete ------------------------------------------------


def test_irrigator_list_now_shows_controls_and_edit_delete(seeded_client):
    resp = seeded_client.get("/clusters/1/irrigators")
    assert resp.status_code == 200
    # start/stop controls reused via partial
    assert 'hx-post="/irrigators/1/start"' in resp.text
    assert 'hx-post="/irrigators/1/stop"' in resp.text
    # edit + delete
    assert 'href="/clusters/1/irrigators/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1/irrigators/1"' in resp.text


def test_irrigator_edit_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/irrigators/1/edit")
    assert resp.status_code == 200
    assert 'value="Test Irrigator"' in resp.text


def test_irrigator_edit_updates(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/irrigators/1/edit",
        data={"name": "Pump X", "type": "tuya_cloud"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp2 = seeded_client.get("/clusters/1/irrigators")
    assert "Pump X" in resp2.text


def test_irrigator_delete_removes_row(seeded_client):
    resp = seeded_client.delete("/clusters/1/irrigators/1")
    assert resp.status_code == 200
    resp2 = seeded_client.get("/clusters/1/irrigators")
    assert "Test Irrigator" not in resp2.text
