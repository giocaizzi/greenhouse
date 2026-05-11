"""Web sensor list and creation form."""


def test_sensors_list_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/sensors")
    assert resp.status_code == 200
    assert "Test Sensor" in resp.text
    assert "Monstera deliciosa" in resp.text  # plant link


def test_new_sensor_form_includes_plant_options(seeded_client):
    resp = seeded_client.get("/clusters/1/sensors/new")
    assert resp.status_code == 200
    assert "Monstera deliciosa" in resp.text  # plant_id <option>


def test_create_sensor(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/sensors",
        data={
            "tuya_device_id": "fake_device_new",
            "name": "Second Sensor",
            "type": "soil_moisture",
            "plant_id": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp2 = seeded_client.get("/clusters/1/sensors")
    assert "Second Sensor" in resp2.text
