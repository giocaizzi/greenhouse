"""Web activity page tests: full-page render and HTMX infinite-scroll pagination."""

import re
import time
from http import HTTPStatus
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from greenhouse_core.models import ENTITY_CLUSTER, SOURCE_IRRIGATION, SOURCE_LEARNING, ActivitySource
from greenhouse_core.repository import IrrigationRepository

PAGE_SIZE = 50  # activity rows per page
SEEDED_EVENTS = 60


def _seed_events(app: FastAPI, count: int, *, base_ts: int | None = None) -> list[int]:
    session = app.state.session_factory()
    try:
        repo = IrrigationRepository(session)
        now = base_ts or int(time.time())
        ids = []
        for i in range(count):
            eid = repo.add_activity_event(
                source=SOURCE_IRRIGATION,
                entity_type=ENTITY_CLUSTER,
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
    """Tests for activity list page."""

    def test_empty_state(self, client: TestClient) -> None:
        """Empty state."""
        resp = client.get("/activity")
        assert resp.status_code == HTTPStatus.OK
        assert "Activity" in resp.text
        assert "No activity yet" in resp.text
        # Filter chips
        assert "?source=irrigation" in resp.text
        assert "?source=learning" in resp.text

    def test_renders_seeded_event(self, app: FastAPI) -> None:
        """Renders seeded event."""
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_events(app, count=1)
            resp = client.get("/activity")
            assert resp.status_code == HTTPStatus.OK
            assert "irrigated" in resp.text
            assert "irrigation" in resp.text

    def test_source_filter(self, app: FastAPI) -> None:
        """Source filter."""
        with TestClient(app, raise_server_exceptions=False) as client:
            session = app.state.session_factory()
            try:
                repo = IrrigationRepository(session)
                repo.add_activity_event(
                    source=SOURCE_LEARNING, entity_type=ENTITY_CLUSTER, code="insight", message="learn msg"
                )
                # "system" is outside the ActivitySource vocabulary on purpose: a second, distinct source to filter out.
                other_source = cast("ActivitySource", "system")
                repo.add_activity_event(source=other_source, entity_type=ENTITY_CLUSTER, code="sync", message="sys msg")
                session.commit()
            finally:
                session.close()

            resp = client.get("/activity?source=learning")
            assert resp.status_code == HTTPStatus.OK
            assert "learn msg" in resp.text
            assert "sys msg" not in resp.text


class TestActivityPagination:
    """Tests for activity pagination."""

    def test_first_page_50_events_with_sentinel(self, app: FastAPI) -> None:
        """Writing 60 events: first page returns 50 rows + a sentinel hx-get attribute."""
        with TestClient(app, raise_server_exceptions=False) as client:
            _seed_events(app, count=SEEDED_EVENTS)
            resp = client.get("/activity")
            assert resp.status_code == HTTPStatus.OK
            # 50 <tr> rows (each event), plus 1 <tr> in thead = 51 total <tr>
            tr_count = resp.text.count("<tr")
            assert tr_count >= PAGE_SIZE + 1  # at least 50 data rows + 1 header
            assert 'hx-get="/activity/page' in resp.text

    def test_second_page_returns_remaining_events(self, app: FastAPI) -> None:
        """Second page fetches the remaining 10 events and has no further sentinel."""
        with TestClient(app, raise_server_exceptions=False) as client:
            now = int(time.time())
            _seed_events(app, count=SEEDED_EVENTS, base_ts=now)

            # Get the first page to find the cursor
            resp1 = client.get("/activity")
            assert resp1.status_code == HTTPStatus.OK
            assert 'hx-get="/activity/page' in resp1.text

            # Extract the before= cursor from the hx-get attr

            m = re.search(r'hx-get="/activity/page\?before=(\d+)"', resp1.text)
            assert m, "No sentinel cursor found in first page"
            cursor = m.group(1)

            # Fetch the second page fragment
            resp2 = client.get(f"/activity/page?before={cursor}")
            assert resp2.status_code == HTTPStatus.OK
            # Remaining 10 data rows + possible date-separator rows — no further sentinel
            tr_count = resp2.text.count("<tr")
            assert tr_count >= SEEDED_EVENTS - PAGE_SIZE
            assert 'hx-trigger="revealed"' not in resp2.text
