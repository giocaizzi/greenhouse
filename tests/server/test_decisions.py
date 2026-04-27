"""Tests for the decision log API."""


class TestDecisionLog:
    def test_list_empty(self, seeded_client):
        resp = seeded_client.get("/api/v1/clusters/1/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_id"] == 1
        assert isinstance(data["items"], list)

    def test_not_found(self, client):
        resp = client.get("/api/v1/clusters/999/decisions")
        assert resp.status_code == 404

    def test_irrigate_writes_decision_log(self, seeded_client):
        """POST /clusters/{id}/irrigate with persist=True writes a decision_log row."""
        resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        assert resp.status_code == 200

        resp = seeded_client.get("/api/v1/clusters/1/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1

        entry = data["items"][0]
        assert entry["cluster_id"] == 1
        assert entry["action"] in ("irrigate", "skip")
        assert "confidence" in entry
        assert "reason_text" in entry
        assert isinstance(entry["reason_text"], str)
        assert len(entry["reason_text"]) > 0

    def test_decision_log_fields(self, seeded_client):
        """Verify all expected DecisionLogResponse fields are present."""
        seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        resp = seeded_client.get("/api/v1/clusters/1/decisions")
        item = resp.json()["items"][0]
        expected_fields = {
            "id",
            "cluster_id",
            "evaluated_at",
            "action",
            "duration_minutes",
            "interval_hours",
            "confidence",
            "primary_code",
            "reason_text",
            "triggered_by",
            "actuated",
        }
        assert expected_fields <= set(item.keys())

    def test_multiple_decisions_ordered_newest_first(self, seeded_client):
        """Multiple pipeline calls produce ordered log entries."""
        seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
        seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})

        resp = seeded_client.get("/api/v1/clusters/1/decisions")
        items = resp.json()["items"]
        assert len(items) >= 2
        timestamps = [i["evaluated_at"] for i in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_param(self, seeded_client):
        """Limit query parameter is respected."""
        for _ in range(3):
            seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})

        resp = seeded_client.get("/api/v1/clusters/1/decisions?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 2

    def test_full_irrigate_also_writes_activity(self, seeded_client):
        """Non-dry-run irrigate writes both a decision log entry and activity event."""
        resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": False, "no_sync": True})
        assert resp.status_code == 200
        action = resp.json()["action"]
        assert action in ("irrigated", "skip", "error")

        # Decision log must have an entry regardless of outcome
        resp = seeded_client.get("/api/v1/clusters/1/decisions")
        assert len(resp.json()["items"]) >= 1

        # Activity timeline must have an entry for this cluster
        resp = seeded_client.get("/api/v1/activity?entity_type=cluster&entity_id=1&source=irrigation")
        assert resp.status_code == 200
        items = resp.json()["items"]
        codes = {i["code"] for i in items}
        assert codes & {"irrigated", "decision_skip"}
