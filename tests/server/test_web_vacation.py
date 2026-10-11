"""Web UI tests for /vacation pages."""

import time
from http import HTTPStatus

from greenhouse_core import utils


class TestVacationListPage:
    """Tests for vacation list page."""

    def test_renders_empty_state(self, client):
        """Renders empty state."""
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Vacation" in resp.text
        assert "No vacation windows" in resp.text

    def test_renders_form(self, client):
        """Renders form."""
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert 'name="starts_at"' in resp.text
        assert 'name="ends_at"' in resp.text
        assert 'name="contact_email"' in resp.text
        assert 'name="notes"' in resp.text


class TestVacationCreate:
    """Tests for vacation create."""

    def test_create_via_form_redirects(self, client):
        """Create via form redirects."""
        now = int(time.time())
        resp = client.post(
            "/vacation",
            data={
                "starts_at": str(now + 3600),
                "ends_at": str(now + 7200),
            },
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.SEE_OTHER
        assert resp.headers["location"] == "/vacation"

    def test_created_window_shows_in_list(self, client):
        """Created window shows in list."""
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now + 3600), "ends_at": str(now + 7200)},
        )
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "scheduled" in resp.text

    def test_create_with_iso_date_strings(self, client):
        """Create with ISO date strings."""
        resp = client.post(
            "/vacation",
            data={"starts_at": "2027-01-01", "ends_at": "2027-01-14"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.SEE_OTHER

    def test_iso_dates_are_midnight_in_the_timezone_preference(self, client, monkeypatch):
        """D8: a date means midnight in the ``timezone`` preference (the zone the page displays), not UTC."""
        monkeypatch.setattr(utils, "_display_timezone", "Europe/Rome")
        resp = client.post(
            "/vacation", data={"starts_at": "2026-05-01", "ends_at": "2026-05-10"}, follow_redirects=False
        )
        assert resp.status_code == HTTPStatus.SEE_OTHER
        (window,) = client.get("/api/v1/vacation").json()["items"]
        assert (window["starts_at"], window["ends_at"]) == (
            1777586400,
            1778364000,
        )  # 00:00 Rome = 22:00Z the day before
        page = client.get(f"/vacation/{window['id']}/edit").text
        assert 'value="2026-05-01"' in page
        assert 'value="2026-05-10"' in page

    def test_active_window_shows_banner(self, client):
        """Active window shows banner."""
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now - 3600), "ends_at": str(now + 7200)},
        )
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Vacation in effect" in resp.text
        assert "active" in resp.text


class TestVacationDelete:
    """Tests for vacation delete."""

    def test_delete_redirects(self, client):
        """Delete redirects."""
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 100), "ends_at": str(now + 200)})
        resp = client.post("/vacation/1/delete", follow_redirects=False)
        assert resp.status_code == HTTPStatus.SEE_OTHER
        assert resp.headers["location"] == "/vacation"

    def test_delete_removes_window(self, client):
        """Delete removes window."""
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 100), "ends_at": str(now + 200)})
        client.post("/vacation/1/delete")
        resp = client.get("/vacation")
        assert "No vacation windows" in resp.text

    def test_delete_not_found_returns_404(self, client):
        """Delete not found returns 404."""
        resp = client.post("/vacation/9999/delete")
        assert resp.status_code == HTTPStatus.NOT_FOUND


class TestVacationWaterBudget:
    """Tests for vacation water budget."""

    def _make_cluster_with_capacity(self, client, reservoir_l, flow_rate_l_per_min):
        client.post("/api/v1/clusters", json={"name": "Budget Cluster"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={
                "tuya_device_id": "fake_budget_irr",
                "name": "Budget Pump",
                "type": "rainpoint.ik10pw",
                "reservoir_l": reservoir_l,
                "flow_rate_l_per_min": flow_rate_l_per_min,
            },
        )

    def test_budget_readout_shown_for_capacity_cluster(self, client):
        """A cluster with capacity + an active vacation renders the budget readout."""
        self._make_cluster_with_capacity(client, reservoir_l=20.0, flow_rate_l_per_min=1.0)
        now = int(time.time())
        # 10-day active window: usable = 20 * 0.95 = 19 L -> 1.9 L/day.
        client.post("/vacation", data={"starts_at": str(now - 3600), "ends_at": str(now + 10 * 86400)})
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Water budget" in resp.text
        assert "Budget Cluster" in resp.text
        assert "Daily budget" in resp.text
        assert "designed to last" in resp.text
        # 20 * 0.95 = 19.0 usable, daily 19/11 days ~ 1.73; just assert the usable marker.
        assert "19.0 L" in resp.text

    def test_no_budget_readout_without_capacity(self, client):
        """A cluster with no capacity configured renders no budget readout, no crash."""
        client.post("/api/v1/clusters", json={"name": "Plain Cluster"})
        client.post(
            "/api/v1/clusters/1/irrigator",
            json={"tuya_device_id": "fake_plain_irr", "name": "Plain Pump", "type": "rainpoint.ik10pw"},
        )
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now - 3600), "ends_at": str(now + 7 * 86400)})
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Water budget" not in resp.text

    def test_no_budget_readout_without_any_window(self, client):
        """With capacity but no scheduled/active window, no budget readout (no crash)."""
        self._make_cluster_with_capacity(client, reservoir_l=20.0, flow_rate_l_per_min=1.0)
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Water budget" not in resp.text

    def test_budget_readout_for_future_scheduled_window(self, client):
        """A future (scheduled, not yet active) window also drives the budget readout."""
        self._make_cluster_with_capacity(client, reservoir_l=20.0, flow_rate_l_per_min=1.0)
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 86400), "ends_at": str(now + 5 * 86400)})
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "Water budget" in resp.text


class TestVacationBannerInBase:
    """Tests for vacation banner in base."""

    def test_active_vacation_shows_banner_on_other_pages(self, client):
        """Active vacation window triggers the banner in _base.html on every page."""
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now - 3600), "ends_at": str(now + 7200)},
        )
        # The dashboard uses _base.html — the banner should appear there too.
        resp = client.get("/")
        assert resp.status_code == HTTPStatus.OK
        assert "Vacation mode is on" in resp.text
