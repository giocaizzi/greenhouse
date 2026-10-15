"""Tests for ntfy push notifications and the DecisionLog.actuated fix."""

from __future__ import annotations

import time
import urllib.request
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar, Literal, Never, Self, TypedDict, cast
from unittest.mock import MagicMock

import pytest

from fake_devices import FakeIrrigatorAdapter, FakeSensorAdapter
from greenhouse_core.constants import ALERT_RENOTIFY_INTERVAL_SECONDS, ALERT_RENOTIFY_OCCURRENCES
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.models import SOURCE_LEAK, TRIGGERED_BY_EMERGENCY, ActivitySource
from greenhouse_core.plant_db import get_plant_database
from greenhouse_server.services.alerts import NewAlert, notify_if_new_alert, raise_alert
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.notify import NtfyClient, maybe_notify
from server.conftest import TEST_NTFY_TOKEN

if TYPE_CHECKING:
    import time_machine
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from conftest import SampleCluster
    from greenhouse_core.repository import IrrigationRepository


class _RecordingNotifier(NtfyClient):
    """Stand-in NtfyClient that records calls instead of doing HTTP.

    A subclass so it can be passed wherever an ``NtfyClient`` is typed; it never calls
    ``NtfyClient.__init__`` because it binds no topic and publishes nothing.
    """

    def __init__(self) -> None:
        self.irrigations: list[dict[str, object]] = []
        self.alerts: list[dict[str, object]] = []

    def notify_irrigation(self, **kwargs: object) -> bool:
        self.irrigations.append(kwargs)
        return True

    def notify_alert(self, **kwargs: object) -> bool:
        self.alerts.append(kwargs)
        return True


class _Prefs:
    """Minimal preferences stand-in for maybe_notify gating tests."""

    def __init__(self, **flags: bool) -> None:
        self.notify_manual = flags.get("notify_manual", True)
        self.notify_emergency = flags.get("notify_emergency", True)
        self.notify_alerts = flags.get("notify_alerts", True)
        self.notify_auto = flags.get("notify_auto", True)


# ── NtfyClient transport ──────────────────────────────────────────────────


class _FakeResponse:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


class _Captured(TypedDict, total=False):
    """What the patched ``urlopen`` saw."""

    request: urllib.request.Request
    timeout: float | None


def _capture_urlopen(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    """Patch urllib.request.urlopen to capture the Request; returns a dict."""
    captured: _Captured = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        captured["request"] = req
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


class TestNtfyClient:
    """Tests for ntfy client."""

    def test_publish_builds_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Publish builds request."""
        captured = _capture_urlopen(monkeypatch)
        timeout = 3
        client = NtfyClient("https://ntfy.example/", "my-topic", token=TEST_NTFY_TOKEN, timeout=timeout)

        ok = client.notify_alert(severity="critical", title="Leak detected", message="cluster 1")

        assert ok is True
        req = captured["request"]
        assert req.full_url == "https://ntfy.example/my-topic"
        assert req.data == b"cluster 1"
        assert captured["timeout"] == timeout
        # urllib capitalizes header keys: "X-Title" -> "X-title".
        assert req.headers["X-title"] == "Leak detected"
        assert req.headers["X-tags"] == "rotating_light"
        assert req.headers["X-priority"] == "5"
        assert req.headers["Authorization"] == f"Bearer {TEST_NTFY_TOKEN}"

    def test_no_token_omits_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No token omits auth header."""
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://ntfy.example", "t").notify_alert(severity="warning", title="x", message="y")
        assert "Authorization" not in captured["request"].headers

    @pytest.mark.parametrize(
        ("severity", "priority", "tag"),
        [
            ("critical", "5", "rotating_light"),
            ("warning", "4", "warning"),
            ("info", "3", "information_source"),
        ],
    )
    def test_severity_mapping(self, monkeypatch: pytest.MonkeyPatch, severity: str, priority: str, tag: str) -> None:
        """Severity mapping."""
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://n", "t").notify_alert(severity=severity, title="t", message="m")
        assert captured["request"].headers["X-priority"] == priority
        assert captured["request"].headers["X-tags"] == tag

    def test_emergency_irrigation_high_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Emergency irrigation high priority."""
        captured = _capture_urlopen(monkeypatch)
        NtfyClient("https://n", "t").notify_irrigation(triggered_by=TRIGGERED_BY_EMERGENCY, irrigator_name="all")
        assert captured["request"].headers["X-priority"] == "5"

    def test_unicode_title_stripped_to_latin1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode title stripped to latin1."""
        captured = _capture_urlopen(monkeypatch)
        # Emoji/CJK in the title would break urllib's latin-1 header encoding.
        NtfyClient("https://n", "t").notify_alert(severity="warning", title="水 plant 🌱", message="m")
        title = captured["request"].headers["X-title"]
        assert title.encode("latin-1")  # does not raise
        assert "plant" in title

    def test_publish_fail_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Publish fail silent."""

        def boom(_req: urllib.request.Request, timeout: float | None = None) -> Never:
            del timeout  # NtfyClient passes it by keyword
            msg = "network down"
            raise OSError(msg)

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        client = NtfyClient("https://n", "t")
        # Must swallow the error and report failure rather than raising.
        assert client.notify_alert(severity="warning", title="x", message="y") is False


# ── maybe_notify gating ─────────────────────────────────────────────────────


class TestMaybeNotify:
    """Tests for maybe notify."""

    def test_skips_when_notifier_none(self) -> None:
        """Skips when notifier none."""
        hits = []
        maybe_notify(None, _Prefs(), "manual", lambda _client: hits.append(1))
        assert hits == []

    def test_skips_when_category_disabled(self) -> None:
        """Skips when category disabled."""
        hits = []
        maybe_notify(_RecordingNotifier(), _Prefs(notify_alerts=False), "alerts", lambda _client: hits.append(1))
        assert hits == []

    def test_fires_when_enabled(self) -> None:
        """Fires when enabled."""
        hits = []
        maybe_notify(_RecordingNotifier(), _Prefs(), "manual", lambda _client: hits.append(1))
        assert hits == [1]

    def test_swallows_callback_error(self) -> None:
        """Swallows callback error."""

        def boom(_client: NtfyClient) -> Never:
            msg = "nope"
            raise RuntimeError(msg)

        # Should not propagate.
        maybe_notify(_RecordingNotifier(), _Prefs(), "manual", boom)


# ── Alert seam (notify_if_new_alert via raise_alert) ─────────────────────────


class TestAlertNotification:
    """Tests for alert notification."""

    def test_new_warning_notifies(self, tmp_db: IrrigationRepository) -> None:
        """New warning notifies."""
        rec = _RecordingNotifier()
        raise_alert(
            tmp_db,
            source=SOURCE_LEAK,
            code="leak_or_stuck_valve",
            title="Leak",
            message="bad",
            severity="critical",
            cluster_id=1,
            notifier=rec,
        )
        assert len(rec.alerts) == 1
        assert rec.alerts[0]["severity"] == "critical"

    def test_info_suppressed(self, tmp_db: IrrigationRepository) -> None:
        """Info suppressed."""
        rec = _RecordingNotifier()
        # "x" is a placeholder outside the ActivitySource vocabulary (stored as-is); the test is about severity.
        placeholder_source = cast("ActivitySource", "x")
        raise_alert(
            tmp_db,
            source=placeholder_source,
            code="c",
            title="t",
            message="m",
            severity="info",
            cluster_id=1,
            notifier=rec,
        )
        assert rec.alerts == []

    def test_second_occurrence_does_not_renotify(self, tmp_db: IrrigationRepository) -> None:
        """A repeat while the alert is open stays silent until the next escalation step."""
        rec = _RecordingNotifier()
        for _ in range(2):
            raise_alert(
                tmp_db,
                source=SOURCE_LEAK,
                code="leak_or_stuck_valve",
                title="Leak",
                message="bad",
                severity="warning",
                cluster_id=1,
                notifier=rec,
            )
        assert len(rec.alerts) == 1

    def test_reopened_alert_renotifies(self, tmp_db: IrrigationRepository) -> None:
        """B-5: a resolved alert that is raised again re-opens and pushes again."""
        rec = _RecordingNotifier()
        kwargs: NewAlert = {
            "source": SOURCE_LEAK,
            "code": "c",
            "title": "t",
            "message": "m",
            "severity": "warning",
            "cluster_id": 1,
        }
        alert = raise_alert(tmp_db, notifier=rec, **kwargs)
        raise_alert(tmp_db, notifier=rec, **kwargs)  # still open: silent
        tmp_db.acknowledge_alert(alert.id)
        raise_alert(tmp_db, notifier=rec, **kwargs)  # acknowledged, but the 3rd occurrence escalates (#146)
        tmp_db.resolve_alert(alert.id)
        reopened = raise_alert(tmp_db, notifier=rec, **kwargs)
        assert (reopened.id, reopened.status, reopened.occurrence_count) == (alert.id, "open", 4)
        pushes = 3  # the opening raise, the 3rd-occurrence step and the re-opening raise
        assert len(rec.alerts) == pushes

    def test_pref_toggle_off_suppresses(self, tmp_db: IrrigationRepository) -> None:
        """Pref toggle off suppresses."""
        tmp_db.update_preferences(notify_alerts=False)
        tmp_db.session.commit()
        rec = _RecordingNotifier()
        raise_alert(
            tmp_db,
            source=SOURCE_LEAK,
            code="leak_or_stuck_valve",
            title="Leak",
            message="bad",
            severity="critical",
            cluster_id=1,
            notifier=rec,
        )
        assert rec.alerts == []

    def test_notify_if_new_alert_none_notifier_is_noop(self, tmp_db: IrrigationRepository) -> None:
        # Should not raise when notifier is None.
        """Notify if new alert none notifier is noop."""
        alert = raise_alert(
            tmp_db, source=SOURCE_LEAK, code="c", title="t", message="m", severity="critical", cluster_id=1
        )
        notify_if_new_alert(tmp_db, None, alert, was_open=False)


class TestAlertEscalation:
    """#146: a warning/critical alert that stays open re-notifies at escalating steps."""

    _CRITICAL: ClassVar[NewAlert] = {
        "source": SOURCE_LEAK,
        "code": "actuation_failed",
        "title": "t",
        "message": "m",
        "severity": "critical",
        "cluster_id": 1,
    }

    def _pushes_per_raise(
        self, repo: IrrigationRepository, clock: time_machine.Traveller, raises: int, *, step: int
    ) -> list[int]:
        """Raise the critical alert ``raises`` times, ``step`` seconds apart; the occurrence counts that pushed."""
        rec = _RecordingNotifier()
        pushed: list[int] = []
        for _ in range(raises):
            before = len(rec.alerts)
            alert = raise_alert(repo, notifier=rec, **self._CRITICAL)
            if len(rec.alerts) > before:
                pushed.append(alert.occurrence_count)
            clock.shift(step)
        return pushed

    def test_pushes_on_open_third_and_tenth(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """Open, 3rd and 10th occurrence push; the others within a day stay silent."""
        assert self._pushes_per_raise(tmp_db, frozen_clock, 20, step=60) == [1, *ALERT_RENOTIFY_OCCURRENCES]

    def test_then_at_most_once_per_interval(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """Past the last step, an hourly repeat pushes once per interval (here: every 24th raise)."""
        hourly = 3600
        pushed = self._pushes_per_raise(tmp_db, frozen_clock, 60, step=hourly)
        assert pushed == [1, 3, 10, 34, 58]

    def test_warning_escalates_like_critical(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """A warning (``actuation_failed`` is one) follows the same steps."""
        rec = _RecordingNotifier()
        warning: NewAlert = {**self._CRITICAL, "severity": "warning"}
        for _ in range(12):
            raise_alert(tmp_db, notifier=rec, **warning)
            frozen_clock.shift(60)
        assert len(rec.alerts) == 1 + len(ALERT_RENOTIFY_OCCURRENCES)

    def test_info_never_pushes(self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller) -> None:
        """Info advisories stay silent at every step."""
        rec = _RecordingNotifier()
        info: NewAlert = {**self._CRITICAL, "severity": "info"}
        for _ in range(12):
            raise_alert(tmp_db, notifier=rec, **info)
            frozen_clock.shift(3600 * 24)
        assert rec.alerts == []

    def test_acknowledge_restarts_the_interval(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """An acknowledged alert stays quiet for a full interval after the acknowledgement."""
        rec = _RecordingNotifier()
        for _ in range(10):
            alert = raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        pushes = len(rec.alerts)
        frozen_clock.shift(ALERT_RENOTIFY_INTERVAL_SECONDS - 60)
        tmp_db.acknowledge_alert(alert.id)
        frozen_clock.shift(120)  # an interval since the last push, not since the ack
        raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        assert len(rec.alerts) == pushes
        frozen_clock.shift(ALERT_RENOTIFY_INTERVAL_SECONDS)
        acked = raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        assert (acked.status, len(rec.alerts)) == ("acknowledged", pushes + 1)

    def test_resolved_then_reopened_pushes_as_new(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """Resolve -> re-raise pushes immediately, whatever the occurrence count."""
        rec = _RecordingNotifier()
        alert = raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        tmp_db.resolve_alert(alert.id)
        frozen_clock.shift(60)
        reopened = raise_alert(tmp_db, notifier=rec, **self._CRITICAL)
        assert (reopened.occurrence_count, len(rec.alerts)) == (3, 2)

    def test_pref_toggle_off_keeps_escalation_silent(
        self, tmp_db: IrrigationRepository, frozen_clock: time_machine.Traveller
    ) -> None:
        """``notify_alerts`` off silences the escalation steps too."""
        tmp_db.update_preferences(notify_alerts=False)
        tmp_db.session.commit()
        assert self._pushes_per_raise(tmp_db, frozen_clock, 12, step=60) == []


# ── Manual / emergency seams via the API ─────────────────────────────────────


class TestManualIrrigationNotifications:
    """Tests for manual irrigation notifications."""

    def test_manual_start_notifies(self, app: FastAPI, seeded_client: TestClient) -> None:
        """Manual start notifies."""
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == HTTPStatus.OK
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "manual"

    def test_manual_stop_notifies(self, app: FastAPI, seeded_client: TestClient) -> None:
        """Manual stop notifies."""
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/stop")
        assert resp.status_code == HTTPStatus.OK
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["detail"] == "stopped"

    def test_log_manual_notifies(self, app: FastAPI, seeded_client: TestClient) -> None:
        """Log manual notifies."""
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 3})
        assert resp.status_code == HTTPStatus.OK
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "manual"

    def test_emergency_stop_all_notifies(self, app: FastAPI, seeded_client: TestClient) -> None:
        """Emergency stop all notifies."""
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        resp = seeded_client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == HTTPStatus.OK
        assert len(rec.irrigations) == 1
        assert rec.irrigations[0]["triggered_by"] == "emergency"

    def test_manual_start_suppressed_when_pref_off(self, app: FastAPI, seeded_client: TestClient) -> None:
        """Manual start suppressed when pref off."""
        rec = _RecordingNotifier()
        app.state.ntfy_notifier = rec
        seeded_client.put("/api/v1/preferences", json={"notify_manual": False})
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == HTTPStatus.OK
        assert rec.irrigations == []

    def test_no_notifier_does_not_break_actuation(self, seeded_client: TestClient) -> None:
        # Default app has no ntfy_notifier; manual start must still succeed.
        """No notifier does not break actuation."""
        resp = seeded_client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
        assert resp.status_code == HTTPStatus.OK


# ── Automated cycle: actuated flip + auto notification ──────────────────────


def _build_dry_cluster(repo: IrrigationRepository) -> tuple[int, int]:
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
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    sensor_id = repo.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_sensor",
        name="Dry Sensor",
        sensor_type="tuya.tr301z",
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


def _build_service(
    repo: IrrigationRepository, notifier: NtfyClient | None
) -> tuple[IrrigationService, FakeIrrigatorAdapter]:
    registry = DeviceRegistry()
    irr = FakeIrrigatorAdapter()
    sensor = FakeSensorAdapter()
    # Registered under the canonical model keys the seeded rows carry (mirrors conftest).
    registry.register_irrigator("rainpoint.ik10pw", lambda: irr)
    registry.register_sensor("tuya.tr301z", lambda: sensor)
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

    def test_persist_records_decision_log_id(self, sample_cluster: SampleCluster) -> None:
        """Persist records decision log ID."""
        db = sample_cluster["db"]
        logic = IrrigationLogic(db, get_plant_database())
        decision = logic.decide_for_cluster(sample_cluster["cluster_id"], current_temp=22.0, persist=True)
        assert decision is not None
        assert decision.decision_log_id is not None
        logs = db.list_decision_logs(sample_cluster["cluster_id"])
        assert logs[0].id == decision.decision_log_id
        # Persisted before actuation, so it starts False.
        assert logs[0].actuated is False

    def test_set_decision_actuated_flips_flag(self, sample_cluster: SampleCluster) -> None:
        """Set decision actuated flips flag."""
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
    """Tests for automated cycle actuated."""

    def test_successful_pipeline_flips_actuated_and_notifies(self, tmp_db: IrrigationRepository) -> None:
        """Successful pipeline flips actuated and notifies."""
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

    def test_dry_run_does_not_flip_actuated(self, tmp_db: IrrigationRepository) -> None:
        """Dry run does not flip actuated."""
        cluster_id, _ = _build_dry_cluster(tmp_db)
        rec = _RecordingNotifier()
        service, _ = _build_service(tmp_db, rec)

        service.run_irrigation_pipeline(cluster_id, dry_run=True, no_sync=True)
        tmp_db.session.commit()

        logs = tmp_db.list_decision_logs(cluster_id)
        assert all(not log.actuated for log in logs)
        assert rec.irrigations == []
