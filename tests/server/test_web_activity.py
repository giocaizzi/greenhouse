"""Web activity page tests: full-page render and HTMX infinite-scroll pagination."""

import time


def _seed_events(app, count: int, *, base_ts: int | None = None) -> list[int]:
    session = app.state.session_factory()
    try:
        from greenhouse_core.repository import IrrigationRepository

        repo = IrrigationRepository(session)
        now = base_ts or int(time.time())
        ids = []
        for i in range(count):
            eid = repo.add_activity_event(
                source="irrigation",
                entity_type="cluster",
                entity_id=1,
                code="irrigated",
                message=f"event {i}",
                severity="info",
                timestamp=now - i,
            )
            ids.append(eid)
        session.commit()
        return ids
    finally:
        session.close()


class TestActivityListPage:
    def test_empty_state(self, client):
        resp = client.get("/activity")
        assert resp.status_code == 200
        assert "Activity" in resp.text
        assert "No activity yet" in resp.text

    def test_renders_seeded_event(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_events(app, count=1)
            resp = client.get("/activity")
            assert resp.status_code == 200
            assert "irrigated" in resp.text
            assert "irrigation" in resp.text

    def test_filter_chips_present(self, client):
        resp = client.get("/activity")
        assert "?source=irrigation" in resp.text
        assert "?source=learning" in resp.text

    def test_source_filter(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                from greenhouse_core.repository import IrrigationRepository

                repo = IrrigationRepository(session)
                repo.add_activity_event(source="learning", entity_type="cluster", code="insight", message="learn msg")
                repo.add_activity_event(source="system", entity_type="cluster", code="sync", message="sys msg")
                session.commit()
            finally:
                session.close()

            resp = client.get("/activity?source=learning")
            assert resp.status_code == 200
            assert "learn msg" in resp.text
            assert "sys msg" not in resp.text


class TestActivityPagination:
    def test_first_page_50_events_with_sentinel(self, app):
        """Writing 60 events: first page returns 50 rows + a sentinel hx-get attribute."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_events(app, count=60)
            resp = client.get("/activity")
            assert resp.status_code == 200
            # 50 <tr> rows (each event), plus 1 <tr> in thead = 51 total <tr>
            tr_count = resp.text.count("<tr")
            assert tr_count >= 51  # at least 50 data rows + 1 header
            assert 'hx-get="/activity/page' in resp.text

    def test_second_page_returns_remaining_events(self, app):
        """Second page fetches the remaining 10 events and has no further sentinel."""
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            now = int(time.time())
            _seed_events(app, count=60, base_ts=now)

            # Get the first page to find the cursor
            resp1 = client.get("/activity")
            assert resp1.status_code == 200
            assert 'hx-get="/activity/page' in resp1.text

            # Extract the before= cursor from the hx-get attr
            import re

            m = re.search(r'hx-get="/activity/page\?before=(\d+)"', resp1.text)
            assert m, "No sentinel cursor found in first page"
            cursor = m.group(1)

            # Fetch the second page fragment
            resp2 = client.get(f"/activity/page?before={cursor}")
            assert resp2.status_code == 200
            # Remaining 10 data rows + possible date-separator rows — no further sentinel
            tr_count = resp2.text.count("<tr")
            assert tr_count >= 10
            assert 'hx-trigger="revealed"' not in resp2.text
