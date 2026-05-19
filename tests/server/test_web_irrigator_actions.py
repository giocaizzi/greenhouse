"""Irrigator start/stop/log-manual web actions."""


def test_irrigator_start_via_web(seeded_client):
    resp = seeded_client.post("/irrigators/1/start", data={"minutes": "3"})
    assert resp.status_code == 200
    # action result partial with success marker
    assert "start" in resp.text.lower()


def test_irrigator_stop_via_web(seeded_client):
    resp = seeded_client.post("/irrigators/1/stop", data={})
    assert resp.status_code == 200
    assert "stop" in resp.text.lower()


def test_log_manual_form_renders(seeded_client):
    resp = seeded_client.get("/irrigators/1/log-manual")
    assert resp.status_code == 200
    assert "Log manual irrigation" in resp.text
    assert 'name="minutes"' in resp.text


def test_log_manual_submit_redirects(seeded_client):
    resp = seeded_client.post(
        "/irrigators/1/log-manual",
        data={"minutes": "5", "notes": "manual watering while away"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # Irrigators are inline on the unified detail page now — land at #irrigators.
    assert resp.headers["location"] == "/clusters/1#irrigators"


def test_irrigator_missing_404(client):
    resp = client.post("/irrigators/9999/start", data={})
    assert resp.status_code == 404
