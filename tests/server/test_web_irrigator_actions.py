"""Irrigator start/stop/log-manual web actions."""

from sqlalchemy.orm import Session

from greenhouse_core.models import IrrigationConfig, IrrigationEvent


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


# ── Web start/stop share the API's code path (watcher, caps, notify, events) ──


class _RecordingNotifier:
    def __init__(self) -> None:
        self.irrigations: list[dict] = []

    def notify_irrigation(self, **kwargs) -> bool:
        self.irrigations.append(kwargs)
        return True

    def notify_alert(self, **kwargs) -> bool:
        return True


def _events(app) -> list[IrrigationEvent]:
    session: Session = app.state.session_factory()
    try:
        return list(session.query(IrrigationEvent).order_by(IrrigationEvent.id))
    finally:
        session.close()


def _set_max_events(app, n: int) -> None:
    session: Session = app.state.session_factory()
    try:
        session.query(IrrigationConfig).filter_by(cluster_id=1).one().max_events_per_day = n
        session.commit()
    finally:
        session.close()


def test_web_start_schedules_pump_watcher(app, seeded_client, running_scheduler):
    """Regression: the web start never scheduled the dry-run watcher."""
    app.state.device_registry = app.state.fake_devices.registry
    resp = seeded_client.post("/irrigators/1/start", data={"minutes": "3"})
    assert resp.status_code == 200
    assert "badge-ok" in resp.text
    assert [j.id for j in running_scheduler.get_jobs() if j.id.startswith("pump-watcher-1-")]


def test_web_start_enforces_daily_caps(app, seeded_client):
    """Regression: the web start skipped max_events_per_day / daily_cap_minutes."""
    _set_max_events(app, 1)
    assert "badge-ok" in seeded_client.post("/irrigators/1/start", data={"minutes": "2"}).text
    resp = seeded_client.post("/irrigators/1/start", data={"minutes": "2"})
    assert resp.status_code == 200  # rendered into the HX result slot, like a device failure
    assert "start failed" in resp.text
    assert "max_events_per_day" in resp.text
    starts = [c for c in app.state.fake_devices.irrigator.calls if c[0] == "start"]
    assert len(starts) == 1, "the capped start must not reach the device"


def test_web_start_notifies(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    seeded_client.post("/irrigators/1/start", data={"minutes": "3"})
    assert [(n["triggered_by"], n["detail"]) for n in rec.irrigations] == [("manual", "started")]


def test_web_start_records_same_event_as_api(app, seeded_client):
    seeded_client.post("/irrigators/1/start", data={"minutes": "3"})
    seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 3})
    web, api = _events(app)
    for field in ("action", "triggered_by", "duration_minutes"):
        assert getattr(web, field) == getattr(api, field), field
    assert (web.action, web.triggered_by, web.duration_minutes) == ("start", "manual", 3)


def test_web_start_device_failure_records_like_api(app, seeded_client):
    app.state.fake_devices.irrigator.start_result = (False, "device timeout")
    resp = seeded_client.post("/irrigators/1/start", data={"minutes": "3"})
    assert "start failed" in resp.text and "device timeout" in resp.text
    assert seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 3}).status_code == 502
    assert _events(app) == []  # neither path records a failed manual start


def test_web_stop_notifies_and_records_same_event_as_api(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    seeded_client.post("/irrigators/1/stop", data={})
    seeded_client.post("/api/v1/irrigators/1/stop")
    web, api = _events(app)
    assert (web.action, web.triggered_by) == (api.action, api.triggered_by)
    assert [n["detail"] for n in rec.irrigations] == ["stopped", "stopped"]


def test_api_start_caps_still_409(app, seeded_client):
    _set_max_events(app, 1)
    assert seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 2}).status_code == 200
    resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 2})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cluster max_events_per_day reached"


# ── Web "log manual watering" shares the API's path too ──────────────────────


def test_web_log_manual_records_same_event_as_api(app, seeded_client):
    """Regression: the web form recorded action="manual", which cooldown, caps,
    learning and efficacy (all keyed on action == "start") never saw."""
    seeded_client.post("/irrigators/1/log-manual", data={"minutes": "4"}, follow_redirects=False)
    seeded_client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 4})
    web, api = _events(app)
    for field in ("action", "triggered_by", "duration_minutes"):
        assert getattr(web, field) == getattr(api, field), field
    assert (web.action, web.triggered_by) == ("start", "manual")
    assert not app.state.fake_devices.irrigator.calls, "logging never actuates"


def test_web_log_manual_enforces_daily_caps(app, seeded_client):
    _set_max_events(app, 1)
    first = seeded_client.post("/irrigators/1/log-manual", data={"minutes": "2"}, follow_redirects=False)
    assert first.status_code == 303
    resp = seeded_client.post("/irrigators/1/log-manual", data={"minutes": "2"}, follow_redirects=False)
    assert resp.status_code == 409
    assert "max_events_per_day" in resp.text
    assert 'name="minutes"' in resp.text  # the form is re-rendered with the error
    assert len(_events(app)) == 1


def test_web_log_manual_notifies(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    seeded_client.post("/irrigators/1/log-manual", data={"minutes": "3"}, follow_redirects=False)
    assert [(n["triggered_by"], n["detail"]) for n in rec.irrigations] == [("manual", "logged (watered by hand)")]
