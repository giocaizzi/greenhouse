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


def test_web_stop_all_without_a_preferences_row_does_not_block_the_page(tmp_path):
    """The notify gate's uncommitted preferences seed must not hold SQLite's write lock for the chrome."""
    import time

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from greenhouse_core.models import UserPreferences
    from greenhouse_server.app import create_app
    from greenhouse_server.config import Settings
    from greenhouse_server.deps import get_device_gateway, get_device_registry

    url = f"sqlite:///{tmp_path}/g.db"
    app = create_app(Settings(db_url=url, enable_scheduler=False, auth_enabled=False), engine=create_engine(url))
    app.dependency_overrides[get_device_registry] = lambda: None
    app.dependency_overrides[get_device_gateway] = lambda: None
    with TestClient(app) as client:
        session = app.state.session_factory()
        session.query(UserPreferences).delete()
        session.commit()
        session.close()
        started = time.monotonic()
        resp = client.post("/bulk/stop-all", headers={"HX-Request": "true"})
        elapsed = time.monotonic() - started
    assert resp.status_code == 200
    assert elapsed < 2  # SQLite busy timeout is 5 s; before the rollback this took ~5.1 s
