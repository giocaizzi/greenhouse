"""Web irrigation config form."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_config_renders_on_unified_detail_page(seeded_client: TestClient) -> None:
    """The legacy /config URL now 301s to the inline #config section on detail."""
    resp = seeded_client.get("/clusters/1/config", follow_redirects=False)
    assert resp.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert resp.headers["location"] == "/clusters/1#config"

    # Follow through: the inline section renders the config form with the
    # seeded values (mode=smart, duration=2, interval=12).
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert 'id="config"' in resp.text
    assert 'value="2"' in resp.text
    assert 'value="12"' in resp.text
    assert "smart" in resp.text


def test_save_config_redirects_to_inline_section(seeded_client: TestClient) -> None:
    """Save config redirects to inline section."""
    resp = seeded_client.post(
        "/clusters/1/config",
        data={"mode": "manual", "duration_minutes": "5", "interval_hours": "24", "auto_run": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["location"] == "/clusters/1#config"

    resp2 = seeded_client.get("/clusters/1")
    assert 'value="5"' in resp2.text


def test_config_section_hidden_without_irrigator(client: TestClient) -> None:
    """Irrigation config is gated on actuation — a sensor-only cluster hides it.

    Mode/duration/interval/quiet-hours/windows only mean something with an
    irrigator, so an irrigator-less cluster shows no Config section at all.
    """
    client.post("/clusters", data={"name": "Sensor-only", "environment": "indoor"}, follow_redirects=False)
    resp = client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert 'id="config"' not in resp.text


def test_config_form_renders_inherit_state_with_irrigator(client: TestClient) -> None:
    """With an irrigator and no config row, every field renders as inherited."""
    client.post("/clusters", data={"name": "Empty", "environment": "indoor"}, follow_redirects=False)
    resp = client.post(
        "/api/v1/clusters/1/irrigator",
        json={"tuya_device_id": "fake_irrigator_001", "name": "Test Irrigator", "type": "rainpoint.ik10pw"},
    )
    assert resp.status_code == HTTPStatus.CREATED
    resp = client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert 'id="config"' in resp.text
    # Without a declared row, every field shows the "↳ default" / "↳ global"
    # badge from _config_field.html.
    assert "↳" in resp.text


def test_save_config_quiet_hours(seeded_client: TestClient) -> None:
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
    assert resp.status_code == HTTPStatus.SEE_OTHER

    # Effective resolver should now report a cluster-level override.
    resp2 = seeded_client.get("/api/v1/clusters/1/config/effective")
    data = resp2.json()
    assert data["effective"]["quiet_start_hour"] == {"value": 22, "source": "cluster"}
    assert data["effective"]["quiet_end_hour"] == {"value": 7, "source": "cluster"}


def test_cluster_cards_show_inherited_effective_config(client: TestClient) -> None:
    """Without a cluster config row the cards show the values in force and where they come from (#200)."""
    client.post("/clusters", data={"name": "Inherits", "environment": "indoor"}, follow_redirects=False)
    resp = client.put("/api/v1/config/global", json={"auto_run": False, "duration_minutes": 7})
    assert resp.status_code == HTTPStatus.OK

    for url in ("/clusters/1/status-fragment", "/clusters/1/card-fragment"):
        html = client.get(url).text
        assert "<kbd>smart</kbd>" in html
        assert "every 12h" in html
        assert "7 min/run" in html
        assert "auto&nbsp;off" in html
        assert "None" not in html
        # duration and auto-run come from the global row, mode and interval from the built-ins
        assert [html.count("&nbsp;global</small>"), html.count("&nbsp;default</small>")] == [2, 2]

    detail = client.get("/clusters/1").text
    assert '<span class="badge-muted">auto off</span>' in detail


def test_cluster_override_has_no_source_tag(client: TestClient) -> None:
    """A cluster-level value is shown bare: only inherited values carry the ↳ tag."""
    client.post("/clusters", data={"name": "Overrides", "environment": "indoor"}, follow_redirects=False)
    client.post(
        "/clusters/1/config",
        data={"mode": "schedule", "duration_minutes": "4", "interval_hours": "6", "auto_run": "true"},
        follow_redirects=False,
    )
    html = client.get("/clusters/1/status-fragment").text
    assert "<kbd>schedule</kbd>" in html
    assert "config-source" not in html


def test_config_form_names_inherited_null(client: TestClient) -> None:
    """An inherited null quiet hour says what it means instead of a bare "inherit"."""
    client.post("/clusters", data={"name": "Quiet", "environment": "indoor"}, follow_redirects=False)
    client.post(
        "/api/v1/clusters/1/irrigator",
        json={"tuya_device_id": "fake_irrigator_001", "name": "Test Irrigator", "type": "rainpoint.ik10pw"},
    )
    client.put("/api/v1/config/global", json={"quiet_start_hour": None, "quiet_end_hour": None})
    html = client.get("/clusters/1").text
    assert 'id="cfg-quiet_start_hour"' in html
    assert html.count('placeholder="inherit (off)"') == len(("quiet_start_hour", "quiet_end_hour"))
