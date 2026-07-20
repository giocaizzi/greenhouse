"""Tests for ntfy push notifications and the DecisionLog.actuated fix."""

import time
import urllib.request
from unittest.mock import MagicMock

import pytest

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.plant_db import get_plant_database
from greenhouse_server.services.alerts import notify_if_new_alert, raise_alert
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.notify import NtfyClient, maybe_notify


class _RecordingNotifier:
    """Stand-in NtfyClient that records calls instead of doing HTTP."""

    def __init__(self) -> None:
        self.irrigations: list[dict] = []
        self.alerts: list[dict] = []

    def notify_irrigation(self, **kwargs) -> bool:
        self.irrigations.append(kwargs)
        return True

    def notify_alert(self, **kwargs) -> bool:
        self.alerts.append(kwargs)
        return True


class _Prefs:
    """Minimal preferences stand-in for maybe_notify gating tests."""

    def __init__(self, **flags) -> None:
        self.notify_manual = flags.get("notify_manual", True)
        self.notify_emergency = flags.get("notify_emergency", True)
        self.notify_alerts = flags.get("notify_alerts", True)
        self.notify_auto = flags.get("notify_auto", True)


# ── NtfyClient transport ──────────────────────────────────────────────────


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(monkeypatch) -> dict:
    """Patch urllib.request.urlopen to capture the Request; returns a dict."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["request"] = req
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


class TestNtfyClient:
    def test_publish_builds_request(self, monkeypatch):
        captured = _capture_urlopen(monkeypatch)
        client = NtfyClient("https://ntfy.example/", "my-topic", token="s3cret", timeout=3)

        ok = client.notify_alert(severity="critical", title="Leak detected", message="cluster 1")

        assert ok is True
        req = captured["request"]
        assert req.full_url == "https://ntfy.example/my-topic"
        assert req.data == b"cluster 1"
        assert captured["timeout"] == 3
        # urllib capitalizes header keys: "X-Title" -> "X-title".
        assert req.headers["X-title"] == "Leak detected"
        assert req.headers["X-tags"] == "rotating_light"
        assert req.headers["X-priority"] == "5"
        assert req.headers["Authorization"] == "Bearer s3cret"

    def test_no_token_omits_auth_header(self, monkeypatch):
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://ntfy.example", "t").notify_alert(severity="warning", title="x", message="y")
        assert "Authorization" not in captured["request"].headers

    @pytest.mark.parametrize(
        "severity,priority,tag",
        [
            ("critical", "5", "rotating_light"),
            ("warning", "4", "warning"),
            ("info", "3", "information_source"),
        ],
    )
    def test_severity_mapping(self, monkeypatch, severity, priority, tag):
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://n", "t").notify_alert(severity=severity, title="t", message="m")
        assert captured["request"].headers["X-priority"] == priority
        assert captured["request"].headers["X-tags"] == tag

    def test_emergency_irrigation_high_priority(self, monkeypatch):
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://n", "t").notify_irrigation(triggered_by="emergency", irrigator_name="all")
        assert captured["request"].headers["X-priority"] == "5"

    def test_unicode_title_stripped_to_latin1(self, monkeypatch):
        captured = _capture_urlopen(monkeypatch)
        # Emoji/CJK in the title would break urllib's latin-1 header encoding.
        NtfyClient("https://n", "t").notify_alert(severity="warning", title="水 plant 🌱", message="m")
        title = captured["request"].headers["X-title"]
        assert title.encode("latin-1")  # does not raise
        assert "plant" in title

    def test_publish_fail_silent(self, monkeypatch):
        def boom(req, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        client = NtfyClient("https://n", "t")
        # Must swallow the error and report failure rather than raising.
        assert client.notify_alert(severity="warning", title="x", message="y") is False


# ── maybe_notify gating ─────────────────────────────────────────────────────


class TestMaybeNotify:
    def test_skips_when_notifier_none(self):
        hits = []
        maybe_notify(None, _Prefs(), "manual", lambda: hits.append(1))
        assert hits == []

    def test_skips_when_category_disabled(self):
        hits = []
        maybe_notify(_RecordingNotifier(), _Prefs(notify_alerts=False), "alerts", lambda: hits.append(1))
        assert hits == []

    def test_fires_when_enabled(self):
        hits = []
        maybe_notify(_RecordingNotifier(), _Prefs(), "manual", lambda: hits.append(1))
        assert hits == [1]

    def test_swallows_callback_error(self):
        def boom():
            raise RuntimeError("nope")

        # Should not propagate.
        maybe_notify(_RecordingNotifier(), _Prefs(), "manual", boom)


# ── Alert seam (notify_if_new_alert via raise_alert) ─────────────────────────


class TestAlertNotification:
    def test_new_warning_notifies(self, tmp_db):
        rec = _RecordingNotifier()
        raise_alert(
            tmp_db,
            source="leak",
            code="leak_or_stuck_valve",
            title="Leak",
            message="bad",
            severity="critical",
            cluster_id=1,
            notifier=rec,
        )
        assert len(rec.alerts) == 1
        assert rec.alerts[0]["severity"] == "critical"

    def test_info_suppressed(self, tmp_db):
        rec = _RecordingNotifier()
        raise_alert(tmp_db, source="x", code="c", title="t", message="m", severity="info", cluster_id=1, notifier=rec)
        assert rec.alerts == []

    def test_repeat_does_not_renotify(self, tmp_db):
        rec = _RecordingNotifier()
        for _ in range(3):
            raise_alert(
                tmp_db,
                source="leak",
                code="leak_or_stuck_valve",
                title="Leak",
                message="bad",
                severity="warning",
                cluster_id=1,
                notifier=rec,
            )
        # Only the first occurrence (occurrence_count == 1) pushes.
        assert len(rec.alerts) == 1

    def test_pref_toggle_off_suppresses(self, tmp_db):
        tmp_db.update_preferences(notify_alerts=False)
        tmp_db.session.commit()
        rec = _RecordingNotifier()
        raise_alert(
            tmp_db,
            source="leak",
            code="leak_or_stuck_valve",
            title="Leak",
            message="bad",
            severity="critical",
            cluster_id=1,
            notifier=rec,
        )
        assert rec.alerts == []

    def test_notify_if_new_alert_none_notifier_is_noop(self, tmp_db):
        # Should not raise when notifier is None.
        alert = raise_alert(tmp_db, source="leak", code="c", title="t", message="m", severity="critical", cluster_id=1)
        notify_if_new_alert(tmp_db, None, alert)


# ── Manual / emergency seams via the API ─────────────────────────────────────


class TestManualIrrigationNotifications:
    def test_manual_start_notifies(self, app, seeded_client):
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == 200
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "manual"

    def test_manual_stop_notifies(self, app, seeded_client):
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/stop")
        assert resp.status_code == 200
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["detail"] == "stopped"

    def test_log_manual_notifies(self, app, seeded_client):
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 3})
        assert resp.status_code == 200
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "manual"

    def test_emergency_stop_all_notifies(self, app, seeded_client):
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == 200
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "emergency"

    def test_manual_start_suppressed_when_pref_off(self, app, seeded_client):
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        seeded_client.put("/api/v1/preferences", json={"notify_manual": False})
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == 200
        assert rec.irrigations == []

    def test_no_notifier_does_not_break_actuation(self, seeded_client):
        # Default app has no ntfy_notifier; manual start must still succeed.
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == 200


# ── Automated cycle: actuated flip + auto notification ──────────────────────


def _build_dry_cluster(repo):
    """Cluster with a thirsty plant + dry sensor readings so the engine irrigates."""
    cluster_id = repo.add_cluster("Dry Cluster")
    plant_id = repo.add_plant(
        cluster_id=cluster_id,
        species="Monstera deliciosa",
        category="tropical",
        water_needs="medium",
        ideal_temp_min=18.0,
        ideal_temp_max=27.0,
        ideal_humidity_min=60.0,
        ideal_humidity_max=80.0,
    )
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_dev",
        name="Dry Irrigator",
        irrigator_type="tuya_cloud",
        config={},
    )
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_sensor",
        name="Dry Sensor",
        sensor_type="soil_moisture",
        config={},
        plant_id=plant_id,
    )
    # All-day irrigation window so the time-of-day gate always allows (keeps
    # the test deterministic regardless of wall-clock at run time).
    repo.add_irrigation_window(cluster_id, start_hour=0, end_hour=24)
    now = int(time.time())
    # Below the target band (so the engine irrigates) but not so low that the
    # critical-stress rule fires and returns before persisting a decision log.
    for offset in range(3):
        repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now - offset * 600, soil_moisture=35.0, temperature=24.0)
    repo.session.commit()
    return cluster_id, irrigator_id


def _build_service(repo, notifier):
    registry = DeviceRegistry()
    irr = FakeIrrigatorAdapter()
    sensor = FakeSensorAdapter()
    # Register under both the seeded type strings and their resolved canonical
    # keys so registry alias resolution finds the fakes (mirrors conftest).
    for key in ("tuya_cloud", "rainpoint.ik10pw"):
        registry.register_irrigator(key, lambda a=irr: a)
    for key in ("soil_moisture", "tuya.tr301z"):
        registry.register_sensor(key, lambda a=sensor: a)
    sync_service = MagicMock()
    sync_service.ensure_fresh_and_read.return_value = {"temperature": 24.0, "soil_moisture": 35.0}
    weather = MagicMock()
    weather.get_current.return_value = {"feels_like": 24.0}
    service = IrrigationService(
        repo=repo,
        registry=registry,
        sync_service=sync_service,
        weather_client=weather,
        plant_db=get_plant_database(),
        notifier=notifier,
    )
    return service, irr


class TestDecisionActuated:
    """Unit coverage for the actuated-flag mechanism (Part B)."""

    def test_persist_records_decision_log_id(self, sample_cluster):
        db = sample_cluster["db"]
        logic = IrrigationLogic(db, get_plant_database())
        decision = logic.decide_for_cluster(sample_cluster["cluster_id"], current_temp=22.0, persist=True)
        assert decision.decision_log_id is not None
        logs = db.list_decision_logs(sample_cluster["cluster_id"])
        assert logs[0].id == decision.decision_log_id
        # Persisted before actuation, so it starts False.
        assert logs[0].actuated is False

    def test_set_decision_actuated_flips_flag(self, sample_cluster):
        db = sample_cluster["db"]
        log_id = db.add_decision_log(
            cluster_id=sample_cluster["cluster_id"],
            evaluated_at=1,
            action="irrigate",
            duration_minutes=5,
            interval_hours=12,
            confidence=0.9,
            primary_code=None,
            reason_text="x",
            payload={},
        )
        db.session.commit()
        db.set_decision_actuated(log_id)
        assert db.list_decision_logs(sample_cluster["cluster_id"])[0].actuated is True


class TestAutomatedCycleActuated:
    def test_successful_pipeline_flips_actuated_and_notifies(self, tmp_db):
        cluster_id, _ = _build_dry_cluster(tmp_db)
        rec = _RecordingNotifier()
        service, irr = _build_service(tmp_db, rec)

        result = service.run_irrigation_pipeline(cluster_id, no_sync=True)
        tmp_db.session.commit()

        assert result["action"] == "irrigated", result
        assert any(c[0] == "start" for c in irr.calls)

        logs = tmp_db.list_decision_logs(cluster_id)
        assert logs[0].actuated is True

        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "auto"

    def test_dry_run_does_not_flip_actuated(self, tmp_db):
        cluster_id, _ = _build_dry_cluster(tmp_db)
        rec = _RecordingNotifier()
        service, _ = _build_service(tmp_db, rec)

        service.run_irrigation_pipeline(cluster_id, dry_run=True, no_sync=True)
        tmp_db.session.commit()

        logs = tmp_db.list_decision_logs(cluster_id)
        assert all(not log.actuated for log in logs)
        assert rec.irrigations == []
