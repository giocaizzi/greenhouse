"""The web kill switch notifies like the JSON API: one emergency push, same text."""

from __future__ import annotations

from .test_notify import _RecordingNotifier


def test_web_stop_all_sends_the_emergency_push(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    resp = seeded_client.post("/bulk/stop-all")
    assert resp.status_code == 200
    assert rec.irrigations == [
        {"triggered_by": "emergency", "irrigator_name": "1 irrigator(s)", "detail": "kill switch"}
    ]


def test_web_and_api_stop_all_push_the_same_payload(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    seeded_client.post("/api/v1/bulk/stop-all")
    seeded_client.post("/bulk/stop-all")
    assert len(rec.irrigations) == 2
    assert rec.irrigations[0] == rec.irrigations[1]


def test_web_stop_all_respects_the_emergency_preference(app, seeded_client):
    rec = _RecordingNotifier()
    app.state.ntfy_notifier = rec
    seeded_client.put("/api/v1/preferences", json={"notify_emergency": False})
    seeded_client.post("/bulk/stop-all")
    assert rec.irrigations == []
