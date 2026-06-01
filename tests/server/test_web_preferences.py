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


def test_preferences_renders_global_config_form(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    assert "Global irrigation defaults" in resp.text
    assert 'action="/config/global"' in resp.text
    assert 'name="quiet_start_hour"' in resp.text
    assert 'name="quiet_end_hour"' in resp.text


def test_global_config_post_persists(client):
    resp = client.post(
        "/config/global",
        data={
            "mode": "smart",
            "duration_minutes": "3",
            "interval_hours": "12",
            "auto_run": "true",
            "quiet_start_hour": "0",
            "quiet_end_hour": "5",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    body = client.get("/api/v1/config/global").json()
    assert body["mode"] == "smart"
    assert body["duration_minutes"] == 3
    assert body["quiet_start_hour"] == 0
    assert body["quiet_end_hour"] == 5
    assert body["auto_run"] is True


def test_global_config_blank_fields_clear_to_inherit(client):
    # Set values first
    client.post(
        "/config/global",
        data={"mode": "smart", "quiet_start_hour": "1", "quiet_end_hour": "6"},
        follow_redirects=False,
    )
    # Resubmit with blanks → null (fall through to constants)
    client.post(
        "/config/global",
        data={"mode": "", "quiet_start_hour": "", "quiet_end_hour": ""},
        follow_redirects=False,
    )
    body = client.get("/api/v1/config/global").json()
    assert body["mode"] is None
    assert body["quiet_start_hour"] is None
    assert body["quiet_end_hour"] is None


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
