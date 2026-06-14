"""Web irrigation config form."""


def test_config_renders_on_unified_detail_page(seeded_client):
    """The legacy /config URL now 301s to the inline #config section on detail."""
    resp = seeded_client.get("/clusters/1/config", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/clusters/1#config"

    # Follow through: the inline section renders the config form with the
    # seeded values (mode=smart, duration=2, interval=12).
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert 'id="config"' in resp.text
    assert 'value="2"' in resp.text
    assert 'value="12"' in resp.text
    assert "smart" in resp.text


def test_save_config_redirects_to_inline_section(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/config",
        data={"mode": "manual", "duration_minutes": "5", "interval_hours": "24", "auto_run": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/clusters/1#config"

    resp2 = seeded_client.get("/clusters/1")
    assert 'value="5"' in resp2.text


def test_config_section_hidden_without_irrigator(client):
    """Irrigation config is gated on actuation — a sensor-only cluster hides it.

    Mode/duration/interval/quiet-hours/windows only mean something with an
    irrigator, so an irrigator-less cluster shows no Config section at all.
    """
    client.post("/clusters", data={"name": "Sensor-only", "environment": "indoor"}, follow_redirects=False)
    resp = client.get("/clusters/1")
    assert resp.status_code == 200
    assert 'id="config"' not in resp.text


def test_config_form_renders_inherit_state_with_irrigator(client):
    """With an irrigator and no config row, every field renders as inherited."""
    client.post("/clusters", data={"name": "Empty", "environment": "indoor"}, follow_redirects=False)
    resp = client.post(
        "/api/v1/clusters/1/irrigator",
        json={"tuya_device_id": "fake_irrigator_001", "name": "Test Irrigator", "type": "tuya_cloud"},
    )
    assert resp.status_code == 201
    resp = client.get("/clusters/1")
    assert resp.status_code == 200
    assert 'id="config"' in resp.text
    # Without a declared row, every field shows the "↳ default" / "↳ global"
    # badge from _config_field.html.
    assert "↳" in resp.text


def test_save_config_quiet_hours(seeded_client):
    """Quiet hours field accepts hour-of-day values and persists them."""
    resp = seeded_client.post(
        "/clusters/1/config",
        data={
            "mode": "smart",
            "duration_minutes": "2",
            "interval_hours": "12",
            "auto_run": "true",
            "quiet_start_hour": "22",
            "quiet_end_hour": "7",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Effective resolver should now report a cluster-level override.
    resp2 = seeded_client.get("/api/v1/clusters/1/config/effective")
    data = resp2.json()
    assert data["effective"]["quiet_start_hour"] == {"value": 22, "source": "cluster"}
    assert data["effective"]["quiet_end_hour"] == {"value": 7, "source": "cluster"}
