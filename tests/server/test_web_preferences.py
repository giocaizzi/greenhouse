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


def test_theme_endpoint_persists_value(client):
    resp = client.post(
        "/preferences/theme",
        data={"theme": "light"},
        follow_redirects=False,
    )
    assert resp.status_code == 204
    assert client.get("/api/v1/preferences").json()["theme"] == "light"


def test_theme_endpoint_only_touches_theme(client):
    # Set non-default neighbours, then flip the theme alone.
    client.post(
        "/preferences",
        data={
            "units": "imperial",
            "timezone": "Europe/Rome",
            "theme": "auto",
            "refresh_interval_seconds": "90",
        },
        follow_redirects=False,
    )
    resp = client.post("/preferences/theme", data={"theme": "dark"})
    assert resp.status_code == 204
    body = client.get("/api/v1/preferences").json()
    assert body["theme"] == "dark"
    # Other prefs are undisturbed.
    assert body["units"] == "imperial"
    assert body["timezone"] == "Europe/Rome"
    assert body["refresh_interval_seconds"] == 90


def test_theme_endpoint_rejects_invalid_value(client):
    resp = client.post("/preferences/theme", data={"theme": "neon"})
    assert resp.status_code == 400
    # Persisted theme is untouched by the rejected request.
    assert client.get("/api/v1/preferences").json()["theme"] != "neon"


def test_persisted_theme_renders_into_html_tag(client):
    # After persisting via the toggle endpoint, a fresh page load (no client
    # localStorage server-side) must paint the saved theme on the <html> tag,
    # so a reload / second device agrees with the stored preference.
    client.post("/preferences/theme", data={"theme": "light"})
    page = client.get("/preferences")
    assert 'data-theme="light"' in page.text


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
    assert 'name="daily_cap_minutes"' in resp.text
    assert 'name="max_events_per_day"' in resp.text


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


def test_global_config_caps_persist(client):
    resp = client.post(
        "/config/global",
        data={"daily_cap_minutes": "45", "max_events_per_day": "3"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    body = client.get("/api/v1/config/global").json()
    assert body["daily_cap_minutes"] == 45
    assert body["max_events_per_day"] == 3

    # Stored values render back into the form inputs.
    page = client.get("/preferences").text
    assert 'value="45"' in page
    assert 'value="3"' in page


def test_global_config_caps_blank_clears_to_inherit(client):
    # Set caps first, then resubmit with blanks → null (no cap enforced).
    client.post(
        "/config/global",
        data={"daily_cap_minutes": "45", "max_events_per_day": "3"},
        follow_redirects=False,
    )
    client.post(
        "/config/global",
        data={"daily_cap_minutes": "", "max_events_per_day": ""},
        follow_redirects=False,
    )
    body = client.get("/api/v1/config/global").json()
    assert body["daily_cap_minutes"] is None
    assert body["max_events_per_day"] is None


def test_global_config_caps_reject_negative(client):
    resp = client.post(
        "/config/global",
        data={"daily_cap_minutes": "-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_preferences_renders_notification_toggles(client):
    resp = client.get("/preferences")
    assert resp.status_code == 200
    for name in ("notify_manual", "notify_emergency", "notify_alerts", "notify_auto"):
        assert f'name="{name}"' in resp.text
    # Default (no ntfy env configured) shows the not-configured hint.
    assert "Not configured" in resp.text


def test_preferences_post_updates_notification_toggles(client):
    # All four start enabled; submit with only notify_manual checked.
    resp = client.post(
        "/preferences",
        data={
            "units": "metric",
            "timezone": "UTC",
            "theme": "auto",
            "refresh_interval_seconds": "30",
            "notify_manual": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    body = client.get("/api/v1/preferences").json()
    assert body["notify_manual"] is True
    # Unchecked categories are absent from the POST and persist as False.
    assert body["notify_emergency"] is False
    assert body["notify_alerts"] is False
    assert body["notify_auto"] is False


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
