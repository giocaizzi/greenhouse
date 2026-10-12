"""Web sensor list and creation form."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_sensors_list_renders(seeded_client: TestClient) -> None:
    """Sensors list renders, with edit and delete actions per row."""
    resp = seeded_client.get("/clusters/1/sensors")
    assert resp.status_code == HTTPStatus.OK
    assert "Test Sensor" in resp.text
    assert "Monstera deliciosa" in resp.text  # plant link
    assert 'href="/clusters/1/sensors/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1/sensors/1"' in resp.text


def test_new_sensor_form_includes_plant_options(seeded_client: TestClient) -> None:
    """New sensor form includes plant options."""
    resp = seeded_client.get("/clusters/1/sensors/new")
    assert resp.status_code == HTTPStatus.OK
    assert "Monstera deliciosa" in resp.text  # plant_id <option>


def test_create_sensor(seeded_client: TestClient) -> None:
    """Create sensor."""
    resp = seeded_client.post(
        "/clusters/1/sensors",
        data={
            "tuya_device_id": "fake_device_new",
            "name": "Second Sensor",
            "type": "tuya.tr301z",
            "plant_id": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER

    resp2 = seeded_client.get("/clusters/1/sensors")
    assert "Second Sensor" in resp2.text
