"""Functional tests for operation endpoints (irrigate, monitor, check, stats, etc.)."""


class TestIrrigate:
    def test_dry_run(self, seeded_client):
        resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] in ("irrigate", "skip")
        assert "reason" in data
        assert "confidence" in data

    def test_irrigate_not_found(self, client):
        resp = client.post("/api/v1/clusters/999/irrigate", json={"dry_run": True})
        assert resp.status_code == 404

    def test_irrigate_with_temp_override(self, seeded_client):
        resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"temp_override": 30.0, "dry_run": True})
        assert resp.status_code == 200

    def test_irrigate_blocked_by_quiet_hours(self, seeded_client):
        """When quiet hours cover the current time, /irrigate returns SKIP with QUIET_HOURS."""
        import datetime as _dt

        hour = _dt.datetime.now(_dt.UTC).hour
        end = (hour + 1) % 24
        if end == hour:
            end = (end + 1) % 24
        seeded_client.put("/api/v1/config/global", json={"quiet_start_hour": hour, "quiet_end_hour": end})

        resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "skip"
        codes = [r["code"] for r in data.get("reasons", [])]
        assert "quiet_hours" in codes

    def test_irrigate_force_bypasses_quiet_hours(self, seeded_client):
        """``force=true`` runs the engine; the decision logs the override warning."""
        import datetime as _dt

        hour = _dt.datetime.now(_dt.UTC).hour
        end = (hour + 1) % 24
        if end == hour:
            end = (end + 1) % 24
        seeded_client.put("/api/v1/config/global", json={"quiet_start_hour": hour, "quiet_end_hour": end})

        resp = seeded_client.post(
            "/api/v1/clusters/1/irrigate",
            json={"dry_run": True, "no_sync": True, "force": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        codes = [r["code"] for r in data.get("reasons", [])]
        # SKIP-via-quiet-hours is bypassed, but the warning is recorded.
        assert "quiet_hours" not in codes
        assert "manual_override_quiet_hours" in codes


class TestMonitor:
    def test_monitor(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_name"] == "Test Cluster"
        assert isinstance(data["sensors"], list)

    def test_monitor_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/monitor")
        assert resp.status_code == 404


class TestCheck:
    def test_check_single(self, seeded_client):
        resp = seeded_client.post("/api/v1/clusters/1/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_id"] == 1
        assert "action" in data

    def test_check_all(self, seeded_client):
        resp = seeded_client.post("/api/v1/check")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["results"], list)
        assert "has_alerts" in data

    def test_check_not_found(self, client):
        resp = client.post("/api/v1/clusters/999/check")
        assert resp.status_code == 404


class TestLearn:
    def test_learn(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/learn")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_name"] == "Test Cluster"
        assert "report" in data

    def test_learn_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/learn")
        assert resp.status_code == 404


class TestStats:
    def test_stats(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_name"] == "Test Cluster"
        assert "total_events" in data

    def test_stats_custom_days(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/stats?days=30")
        assert resp.status_code == 200

    def test_stats_export(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/stats/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"

    def test_stats_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/stats")
        assert resp.status_code == 404


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["jobs"], list)


class TestSchedulerRoutes:
    def test_list_jobs(self, client):
        resp = client.get("/api/v1/scheduler/jobs")
        assert resp.status_code == 200
        jobs = resp.json()
        assert isinstance(jobs, list)
        # Default jobs should be registered
        job_ids = [j["id"] for j in jobs]
        assert "sensor_sync" in job_ids
        assert "check_all" in job_ids


class TestFullLifecycle:
    def test_create_to_irrigate(self, client):
        """Full lifecycle: create cluster → add plant → sensor → irrigator → config → irrigate."""
        # Setup
        client.post("/api/v1/clusters", json={"name": "Lifecycle Test"})
        client.post("/api/v1/clusters/1/plants", json={"species": "Fern", "water_needs": "high"})
        client.post(
            "/api/v1/clusters/1/sensors",
            json={"tuya_device_id": "s1", "name": "S1", "type": "soil_moisture", "plant_id": 1},
        )
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "i1", "name": "Pump", "type": "tuya_cloud"},
        )
        client.put(
            "/api/v1/clusters/1/config",
            json={"mode": "smart", "duration_minutes": 2, "interval_hours": 12},
        )

        # Verify status
        resp = client.get("/api/v1/clusters/1/status")
        assert resp.status_code == 200
        status = resp.json()
        assert len(status["plants"]) == 1
        assert len(status["sensors"]) == 1
        assert len(status["irrigators"]) == 1

        # Dry-run irrigate
        resp = client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        assert resp.status_code == 200
        assert resp.json()["action"] in ("irrigate", "skip")

        # Check
        resp = client.post("/api/v1/clusters/1/check")
        assert resp.status_code == 200
