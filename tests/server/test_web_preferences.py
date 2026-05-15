"""Web preferences page."""


def test_preferences_page_renders(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    assert "Preferences" in resp.text
    assert 'name="units"' in resp.text
    assert 'name="timezone"' in resp.text
    assert 'name="theme"' in resp.text
    assert 'name="refresh_interval_seconds"' in resp.text


def test_preferences_post_updates(client):
    resp = client.post(
        "/preferences",
        data={
            "units": "imperial",
            "timezone": "Europe/Rome",
            "theme": "dark",
            "refresh_interval_seconds": "60",
            "default_cluster_id": "",
            "dry_run_global": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp2 = client.get("/api/v1/preferences")
    body = resp2.json()
    assert body["units"] == "imperial"
    assert body["timezone"] == "Europe/Rome"
    assert body["theme"] == "dark"
    assert body["refresh_interval_seconds"] == 60
    assert body["dry_run_global"] is True


def test_preferences_includes_default_cluster_options(seeded_client):
    resp = seeded_client.get("/preferences")
    assert resp.status_code == 200
    assert "Test Cluster" in resp.text


def test_preferences_clearing_dry_run_persists_false(client):
    # First turn it on
    client.post(
        "/preferences",
        data={
            "units": "metric",
            "timezone": "UTC",
            "theme": "auto",
            "refresh_interval_seconds": "30",
            "dry_run_global": "on",
        },
        follow_redirects=False,
    )
    # Then turn it off (checkbox not sent)
    client.post(
        "/preferences",
        data={
            "units": "metric",
            "timezone": "UTC",
            "theme": "auto",
            "refresh_interval_seconds": "30",
        },
        follow_redirects=False,
    )
    body = client.get("/api/v1/preferences").json()
    assert body["dry_run_global"] is False
