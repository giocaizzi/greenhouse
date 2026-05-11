"""Web irrigation config form."""


def test_config_form_renders_with_existing_config(seeded_client):
    resp = seeded_client.get("/clusters/1/config")
    assert resp.status_code == 200
    assert "Irrigation config" in resp.text
    # seeded_client sets mode=smart, duration=2, interval=12
    assert 'value="2"' in resp.text
    assert 'value="12"' in resp.text
    assert "smart" in resp.text


def test_save_config(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/config",
        data={"mode": "manual", "duration_minutes": "5", "interval_hours": "24", "auto_run": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/clusters/1"

    resp2 = seeded_client.get("/clusters/1/config")
    assert 'value="5"' in resp2.text


def test_config_form_for_new_cluster_has_defaults(client):
    client.post("/clusters", data={"name": "Empty", "environment": "indoor"}, follow_redirects=False)
    resp = client.get("/clusters/1/config")
    assert resp.status_code == 200
    # Default mode=smart selected; empty duration/interval fields
    assert "smart" in resp.text
