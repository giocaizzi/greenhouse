"""Characterization tests for route/web branches WP5 restructures (refactor plan §0.4 G.1).

Each test pins what the code does today on a branch the WP5 task subsets did not exercise:
the plant-DB sync (API + web), the events CSV export without an irrigator, the cluster
detail rationale fallback, the plant dashboard's no-irrigator / relative-time branches,
``base_context``'s swallow paths and ``create_app``'s default settings/engine.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from golden import FROZEN_TS
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.web.context import base_context

CSV_HEADER = "timestamp,date,time,irrigator,action,duration_minutes,triggered_by,notes\r\n"


def _repo_session(app):
    session = app.state.session_factory()
    return IrrigationRepository(session), session


def _seed(
    app, plants_by_cluster: dict[str, list[str]], *, irrigator_for: str | None = None, sensor_for: str | None = None
) -> dict[str, int]:
    """Create clusters with plants (species), optionally one irrigator / sensor; return name → id."""
    repo, session = _repo_session(app)
    ids: dict[str, int] = {}
    try:
        for cluster_name, species_list in plants_by_cluster.items():
            cid = repo.add_cluster(name=cluster_name)
            ids[cluster_name] = cid
            for species in species_list:
                ids[species] = repo.add_plant(cluster_id=cid, species=species)
            if cluster_name == irrigator_for:
                ids["irrigator"] = repo.add_irrigator(
                    cluster_id=cid,
                    tuya_device_id="fake_tuya_device_aabbccdd",
                    name="Pump",
                    irrigator_type="fake.irrigator",
                    config={},
                )
            if cluster_name == sensor_for:
                ids["sensor"] = repo.add_sensor(
                    cluster_id=cid,
                    tuya_device_id="fake_tuya_device_11223344",
                    name="Probe",
                    sensor_type="fake.sensor",
                    config={},
                )
        session.commit()
    finally:
        session.close()
    return ids


def _plant_notes(app, plant_id: int) -> str | None:
    repo, session = _repo_session(app)
    try:
        plant = repo.get_plant(plant_id)
        assert plant is not None
        return plant.notes
    finally:
        session.close()


@pytest.fixture
def failing_species(app, monkeypatch):
    """Make the plant DB raise for species "Bad" (every other species resolves normally)."""
    plant_db = app.state.plant_db
    original = plant_db.get_care_data

    def get_care_data(species=None, category=None):
        if species == "Bad":
            raise ValueError("boom")
        return original(species=species, category=category)

    monkeypatch.setattr(plant_db, "get_care_data", get_care_data)


# ── API POST /plants/sync ────────────────────────────────────────────────────


def test_api_sync_plant_id_found_in_a_later_cluster_syncs_only_that_plant(app, client):
    ids = _seed(app, {"C1": ["Fern"], "C2": ["Monstera deliciosa"]})
    resp = client.post("/api/v1/plants/sync", json={"plant_id": ids["Monstera deliciosa"]})
    assert resp.status_code == 200
    assert resp.json() == {"synced": 1, "errors": []}
    assert _plant_notes(app, ids["Monstera deliciosa"]).startswith("Sources: ")
    assert _plant_notes(app, ids["Fern"]) is None


def test_api_sync_unknown_plant_id_is_404(app, client):
    _seed(app, {"C1": ["Fern"]})
    resp = client.post("/api/v1/plants/sync", json={"plant_id": 999})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Plant 999 not found"}


def test_api_sync_plant_scan_does_not_find_orphan_plants(app, client):
    """The lookup scans clusters' plants, so a plant whose cluster row is gone is "not found"."""
    _seed(app, {"C1": ["Fern"]})
    repo, session = _repo_session(app)
    try:
        orphan = repo.add_plant(cluster_id=999, species="Orphan")
        session.commit()
    finally:
        session.close()
    resp = client.post("/api/v1/plants/sync", json={"plant_id": orphan})
    assert resp.status_code == 404
    assert resp.json() == {"detail": f"Plant {orphan} not found"}


def test_api_sync_unknown_cluster_is_404(app, client):
    """D12 (was B-16): an unknown cluster_id is a 404, not a silent ``synced=0``."""
    _seed(app, {"C1": ["Fern"]})
    resp = client.post("/api/v1/plants/sync", json={"cluster_id": 999})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Cluster not found"}


def test_api_sync_plant_id_zero_means_every_cluster(app, client):
    _seed(app, {"C1": ["Fern"], "C2": ["Monstera deliciosa"]})
    resp = client.post("/api/v1/plants/sync", json={"plant_id": 0})
    assert resp.status_code == 200
    assert resp.json() == {"synced": 2, "errors": []}


def test_api_sync_collects_per_plant_errors_and_keeps_going(app, client, failing_species):
    ids = _seed(app, {"C1": ["Bad", "Fern"]})
    resp = client.post("/api/v1/plants/sync", json={"cluster_id": ids["C1"]})
    assert resp.status_code == 200
    assert resp.json() == {"synced": 1, "errors": ["Bad: boom"]}
    assert _plant_notes(app, ids["Fern"]).startswith("Sources: ")


def test_api_sync_single_plant_error_is_not_caught(app, client, failing_species):
    ids = _seed(app, {"C1": ["Bad"]})
    resp = client.post("/api/v1/plants/sync", json={"plant_id": ids["Bad"]})
    assert resp.status_code == 500


# ── Web POST /plants/sync ────────────────────────────────────────────────────


def test_web_sync_unknown_cluster_is_404(app, client):
    """D12 (was B-16): an unknown cluster id is a 404 (same as the API), not "0 plants synced"."""
    _seed(app, {"C1": ["Fern"]})
    resp = client.post("/plants/sync", data={"cluster_id": "999"})
    assert resp.status_code == 404
    assert "plants synced" not in resp.text
    assert "Cluster not found" in resp.text


def test_web_sync_collects_per_plant_errors_and_keeps_going(app, client, failing_species):
    ids = _seed(app, {"C1": ["Bad", "Fern"]})
    resp = client.post("/plants/sync", data={"cluster_id": str(ids["C1"])})
    assert resp.status_code == 200
    assert '<span class="mono">1</span> plant synced' in resp.text
    assert "<li>Bad: boom</li>" in resp.text
    assert _plant_notes(app, ids["Fern"]).startswith("Sources: ")


def test_web_sync_plant_scan_does_not_find_orphan_plants(app, client):
    _seed(app, {"C1": ["Fern"]})
    repo, session = _repo_session(app)
    try:
        orphan = repo.add_plant(cluster_id=999, species="Orphan")
        session.commit()
    finally:
        session.close()
    resp = client.post("/plants/sync", data={"plant_id": str(orphan)})
    assert resp.status_code == 404
    assert f"Plant {orphan} not found" in resp.text


# ── API GET /clusters/{id}/stats/export ──────────────────────────────────────


def test_api_stats_export_without_irrigator_is_header_only(app, client):
    ids = _seed(app, {"C1": ["Fern"]})
    resp = client.get(f"/api/v1/clusters/{ids['C1']}/stats/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == f"attachment; filename=cluster_{ids['C1']}_stats.csv"
    assert resp.text == CSV_HEADER


# ── Web GET /clusters/{id} — rationale fallback ──────────────────────────────


def test_web_cluster_detail_with_undecodable_decision_payload_shows_no_rationale(app, client):
    ids = _seed(app, {"C1": ["Fern"]}, irrigator_for="C1", sensor_for="C1")
    repo, session = _repo_session(app)
    try:
        log_id = repo.add_decision_log(
            cluster_id=ids["C1"],
            evaluated_at=FROZEN_TS,
            action="skip",
            duration_minutes=0,
            interval_hours=24,
            confidence=0.5,
            primary_code=None,
            reason_text="r",
            payload={"reasons": [{"message": "should not render", "code": "x"}]},
        )
        session.commit()
        from greenhouse_core.models import DecisionLog

        session.get(DecisionLog, log_id).payload_json = "not json"
        session.commit()
    finally:
        session.close()
    resp = client.get(f"/clusters/{ids['C1']}")
    assert resp.status_code == 200
    assert "rationale__row" not in resp.text
    assert "should not render" not in resp.text
    assert "No decisions yet." in resp.text


# ── Web GET /clusters/{cid}/plants/{pid} — plant dashboard ────────────────────


def test_web_plant_dashboard_without_irrigator_renders_sensor_only(app, client, frozen_clock):
    ids = _seed(app, {"C1": ["Fern"]}, sensor_for="C1")
    resp = client.get(f"/clusters/{ids['C1']}/plants/{ids['Fern']}")
    assert resp.status_code == 200
    assert "sensor-only cluster" in resp.text
    assert "irrigated " not in resp.text


@pytest.mark.parametrize(
    ("ages", "expected"),
    [
        ((600, 7200), "irrigated 10m ago"),
        ((3 * 86400 + 5, 5 * 86400), "irrigated 3d ago"),
        # D7: the shared ``age_seconds`` filter — seconds under a minute, "stale" from 7 days, "—" when none.
        ((30,), "irrigated 30s ago"),
        ((8 * 86400,), "irrigated stale"),
        ((), "irrigated —"),
    ],
)
def test_web_plant_dashboard_relative_time_of_newest_event(app, client, frozen_clock, ages, expected):
    ids = _seed(app, {"C1": ["Fern"]}, irrigator_for="C1")
    repo, session = _repo_session(app)
    try:
        for age in ages:
            repo.add_irrigation_event(
                ids["irrigator"], action="start", triggered_by="manual", timestamp=FROZEN_TS - age
            )
        session.commit()
    finally:
        session.close()
    resp = client.get(f"/clusters/{ids['C1']}/plants/{ids['Fern']}")
    assert resp.status_code == 200
    assert expected in resp.text


# ── web/context.base_context swallow paths ───────────────────────────────────


class _ClosingSession:
    """Stand-in session: any repository query on it fails; close() is recorded."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _request(**state):
    return SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace(**state)))


BASE_KEYS = [
    "request",
    "is_hx",
    "now_text",
    "dry_run_global",
    "active_vacation",
    "scheduler_paused",
    "theme",
    "app_version",
    "auth_enabled",
    "show_chrome",
]


def test_base_context_without_session_factory_uses_defaults(clean_env, frozen_clock):
    request = _request(settings=SimpleNamespace(auth_enabled=False))
    ctx = base_context(request, extra_key=1)
    assert list(ctx) == [*BASE_KEYS, "extra_key"]
    assert ctx["request"] is request
    assert {k: v for k, v in ctx.items() if k not in ("request", "app_version")} == {
        "is_hx": False,
        "now_text": "2026-04-15 10:00",
        "dry_run_global": False,
        "active_vacation": None,
        "scheduler_paused": False,
        "theme": "auto",
        "auth_enabled": False,
        "show_chrome": True,
        "extra_key": 1,
    }


def test_base_context_swallows_preference_errors_and_closes_session():
    session = _ClosingSession()
    ctx = base_context(_request(session_factory=lambda: session, settings=SimpleNamespace(auth_enabled=True)))
    assert session.closed == 1
    assert (ctx["dry_run_global"], ctx["active_vacation"], ctx["scheduler_paused"], ctx["theme"]) == (
        False,
        None,
        False,
        "auto",
    )


def test_base_context_keeps_preferences_read_before_a_vacation_error(monkeypatch):
    prefs = SimpleNamespace(dry_run_global=True, scheduler_paused=True, theme="dark")
    monkeypatch.setattr(IrrigationRepository, "get_preferences", lambda self: prefs)

    def boom(self):
        raise RuntimeError("vacation read failed")

    monkeypatch.setattr(IrrigationRepository, "get_active_vacation", boom)
    session = _ClosingSession()
    ctx = base_context(_request(session_factory=lambda: session, settings=SimpleNamespace(auth_enabled=True)))
    assert session.closed == 1
    assert (ctx["dry_run_global"], ctx["active_vacation"], ctx["scheduler_paused"], ctx["theme"]) == (
        True,
        None,
        True,
        "dark",
    )


def test_base_context_auth_enabled_defaults_true_without_settings():
    assert base_context(_request())["auth_enabled"] is True


# ── create_app defaults ──────────────────────────────────────────────────────


def _memory_engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def test_create_app_without_settings_reads_settings_from_env(clean_env, monkeypatch, tmp_path):
    db_url = f"sqlite:///{tmp_path}/env.db"
    monkeypatch.setenv("IRRIGATION_DB_URL", db_url)
    monkeypatch.setenv("IRRIGATION_ENABLE_SCHEDULER", "false")
    engine = _memory_engine()
    try:
        application = create_app(engine=engine)
        assert isinstance(application.state.settings, Settings)
        assert application.state.settings.db_url == db_url
        assert application.state.session_factory.kw["bind"] is engine
    finally:
        engine.dispose()


def test_create_app_without_engine_builds_one_from_settings_db_url(clean_env, tmp_path):
    db_url = f"sqlite:///{tmp_path}/built.db"
    application = create_app(Settings(db_url=db_url, enable_scheduler=False))
    engine = application.state.session_factory.kw["bind"]
    try:
        assert str(engine.url) == db_url
        assert (tmp_path / "built.db").exists()
    finally:
        engine.dispose()


# ── D15: GET /clusters/{id}/monitor (API) and the web monitor share one syncing path ──────────


@pytest.mark.parametrize("url", ["/api/v1/clusters/{cid}/monitor", "/clusters/{cid}/monitor"])
def test_monitor_runs_the_freshness_sync_and_commits_its_rows(app, client, monkeypatch, url):
    """D15 (was B-N1): both monitors refresh stale sensors and the refreshed reading is persisted."""
    from greenhouse_server.services.sync import SyncService

    ids = _seed(app, {"C1": ["Fern"]}, sensor_for="C1")
    calls: list[int] = []

    def fake_ensure_fresh(self, cluster_id):
        # Stands in for the Cloud: writes one fresh row the way sync_single_sensor does, then flushes.
        calls.append(cluster_id)
        (sensor,) = self._repo.get_sensors_in_cluster(cluster_id)
        self._repo.add_sensor_reading(sensor_id=sensor.id, timestamp=FROZEN_TS - 60, soil_moisture=41.0)
        self._repo.session.flush()

    monkeypatch.setattr(SyncService, "ensure_fresh_and_read", fake_ensure_fresh)
    resp = client.get(url.format(cid=ids["C1"]))
    assert resp.status_code == 200
    assert calls == [ids["C1"]]
    repo, session = _repo_session(app)
    try:
        (sensor,) = repo.get_sensors_in_cluster(ids["C1"])
        latest = repo.get_latest_reading(sensor.id)
        assert (latest.timestamp, latest.soil_moisture) == (FROZEN_TS - 60, 41.0)
    finally:
        session.close()
