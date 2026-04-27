"""Tests for the activity timeline API."""

import time


class TestActivityList:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["next_cursor"] is None

    def _seed_events(self, app, count: int = 5) -> list[int]:
        session = app.state.session_factory()
        try:
            from tuya_irrigation_core.repository import IrrigationRepository

            repo = IrrigationRepository(session)
            now = int(time.time())
            ids = []
            for i in range(count):
                eid = repo.add_activity_event(
                    source="irrigation",
                    entity_type="cluster",
                    entity_id=1,
                    code="irrigated",
                    message=f"event {i}",
                    severity="info",
                    timestamp=now - i * 60,
                )
                ids.append(eid)
            session.commit()
            return ids
        finally:
            session.close()

    def test_ordering_newest_first(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            self._seed_events(app, count=3)
            resp = client.get("/api/v1/activity")
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) == 3
            timestamps = [i["timestamp"] for i in items]
            assert timestamps == sorted(timestamps, reverse=True)

    def test_filter_by_entity_type(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                repo.add_activity_event(
                    source="system", entity_type="sensor", entity_id=99, code="stale", message="sensor stale"
                )
                repo.add_activity_event(
                    source="irrigation", entity_type="cluster", entity_id=1, code="irrigated", message="ok"
                )
                session.commit()
            finally:
                session.close()

            resp = client.get("/api/v1/activity?entity_type=sensor")
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert all(i["entity_type"] == "sensor" for i in items)
            assert len(items) == 1

    def test_filter_by_source(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                repo.add_activity_event(source="irrigation", entity_type="cluster", code="irrigated", message="a")
                repo.add_activity_event(source="learning", entity_type="cluster", code="insight", message="b")
                session.commit()
            finally:
                session.close()

            resp = client.get("/api/v1/activity?source=learning")
            items = resp.json()["items"]
            assert all(i["source"] == "learning" for i in items)

    def test_filter_by_severity(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from tuya_irrigation_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                repo.add_activity_event(
                    source="irrigation", entity_type="cluster", code="ok", message="info msg", severity="info"
                )
                repo.add_activity_event(
                    source="irrigation",
                    entity_type="cluster",
                    code="fail",
                    message="warning msg",
                    severity="warning",
                )
                session.commit()
            finally:
                session.close()

            resp = client.get("/api/v1/activity?severity=warning")
            items = resp.json()["items"]
            assert all(i["severity"] == "warning" for i in items)

    def test_cursor_pagination(self, app):
        """When limit is reached, next_cursor equals the oldest item's timestamp."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            self._seed_events(app, count=5)

            resp = client.get("/api/v1/activity?limit=3")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 3
            assert data["next_cursor"] == data["items"][-1]["timestamp"]

            # Fetch next page using cursor
            cursor = data["next_cursor"]
            resp2 = client.get(f"/api/v1/activity?limit=3&before={cursor}")
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert len(data2["items"]) == 2
            assert data2["next_cursor"] is None

    def test_no_cursor_when_fewer_items_than_limit(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            self._seed_events(app, count=2)
            resp = client.get("/api/v1/activity?limit=10")
            data = resp.json()
            assert data["next_cursor"] is None
