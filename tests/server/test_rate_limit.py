"""Tests for the per-cluster daily rate-limit guards on /start and /log-manual."""

from sqlalchemy.orm import Session

from greenhouse_core.models import IrrigationConfig


def _set_cap(client, cluster_id: int, *, max_events_per_day: int | None = None, daily_cap_minutes: int | None = None):
    """Patch rate-limit fields on the IrrigationConfig directly in the DB."""
    session: Session = client.app.state.session_factory()
    try:
        config = session.query(IrrigationConfig).filter_by(cluster_id=cluster_id).one()
        if max_events_per_day is not None:
            config.max_events_per_day = max_events_per_day
        if daily_cap_minutes is not None:
            config.daily_cap_minutes = daily_cap_minutes
        session.commit()
    finally:
        session.close()


def _setup_cluster_irrigator_config(client, *, tuya_id: str = "fake_rl_001") -> int:
    """Create cluster + irrigator + base config; return irrigator id."""
    client.post("/api/v1/clusters", json={"name": "Rate Limit Cluster"})
    resp = client.post(
        "/api/v1/clusters/1/irrigators",
        json={"tuya_device_id": tuya_id, "name": "Pump", "type": "tuya_cloud"},
    )
    assert resp.status_code == 201
    client.put(
        "/api/v1/clusters/1/config",
        json={"mode": "smart", "duration_minutes": 5, "interval_hours": 12, "auto_run": True},
    )
    return resp.json()["id"]


class TestMaxEventsPerDay:
    def test_start_blocked_after_cap(self, client):
        """Third /start returns 409 when max_events_per_day=2."""
        _setup_cluster_irrigator_config(client, tuya_id="fake_cap_001")
        _set_cap(client, 1, max_events_per_day=2)

        r1 = client.post("/api/v1/irrigators/1/start", json={"minutes": 2})
        assert r1.status_code == 200
        r2 = client.post("/api/v1/irrigators/1/start", json={"minutes": 2})
        assert r2.status_code == 200
        r3 = client.post("/api/v1/irrigators/1/start", json={"minutes": 2})
        assert r3.status_code == 409
        assert "max_events_per_day" in r3.json()["detail"]

    def test_log_manual_blocked_after_cap(self, client):
        """Third /log-manual returns 409 when max_events_per_day=2."""
        _setup_cluster_irrigator_config(client, tuya_id="fake_cap_002")
        _set_cap(client, 1, max_events_per_day=2)

        r1 = client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 2})
        assert r1.status_code == 200
        r2 = client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 2})
        assert r2.status_code == 200
        r3 = client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 2})
        assert r3.status_code == 409
        assert "max_events_per_day" in r3.json()["detail"]

    def test_no_config_allows_unlimited(self, client):
        """Without a config, no cap is enforced."""
        client.post("/api/v1/clusters", json={"name": "No Config Cluster"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "fake_nc_001", "name": "Pump", "type": "tuya_cloud"},
        )

        for _ in range(5):
            r = client.post("/api/v1/irrigators/1/start", json={"minutes": 1})
            assert r.status_code == 200


class TestDailyCapMinutes:
    def test_start_blocked_by_duration_cap(self, client):
        """10-minute daily cap blocks a request that would exceed it."""
        _setup_cluster_irrigator_config(client, tuya_id="fake_dc_001")
        _set_cap(client, 1, daily_cap_minutes=10)

        r1 = client.post("/api/v1/irrigators/1/start", json={"minutes": 6})
        assert r1.status_code == 200

        # 6 + 6 = 12 > 10
        r2 = client.post("/api/v1/irrigators/1/start", json={"minutes": 6})
        assert r2.status_code == 409
        assert "daily cap" in r2.json()["detail"]

    def test_start_allowed_within_duration_cap(self, client):
        """Requests that cumulatively stay within the cap succeed."""
        _setup_cluster_irrigator_config(client, tuya_id="fake_dc_002")
        _set_cap(client, 1, daily_cap_minutes=10)

        r1 = client.post("/api/v1/irrigators/1/start", json={"minutes": 4})
        assert r1.status_code == 200

        r2 = client.post("/api/v1/irrigators/1/start", json={"minutes": 4})
        assert r2.status_code == 200  # 4 + 4 = 8 <= 10

    def test_log_manual_blocked_by_duration_cap(self, client):
        """10-minute cap applies to /log-manual as well."""
        _setup_cluster_irrigator_config(client, tuya_id="fake_dc_003")
        _set_cap(client, 1, daily_cap_minutes=10)

        r1 = client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 6})
        assert r1.status_code == 200

        r2 = client.post("/api/v1/irrigators/1/log-manual", json={"minutes": 6})
        assert r2.status_code == 409
        assert "daily cap" in r2.json()["detail"]
