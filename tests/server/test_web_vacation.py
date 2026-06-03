"""Web UI tests for /vacation pages."""

import time


class TestVacationListPage:
    def test_renders_empty_state(self, client):
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "Vacation" in resp.text
        assert "No vacation windows" in resp.text

    def test_renders_form(self, client):
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert 'name="starts_at"' in resp.text
        assert 'name="ends_at"' in resp.text
        assert 'name="contact_email"' in resp.text
        assert 'name="notes"' in resp.text


class TestVacationCreate:
    def test_create_via_form_redirects(self, client):
        now = int(time.time())
        resp = client.post(
            "/vacation",
            data={
                "starts_at": str(now + 3600),
                "ends_at": str(now + 7200),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/vacation"

    def test_created_window_shows_in_list(self, client):
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now + 3600), "ends_at": str(now + 7200)},
        )
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "scheduled" in resp.text

    def test_create_with_iso_date_strings(self, client):
        resp = client.post(
            "/vacation",
            data={"starts_at": "2027-01-01", "ends_at": "2027-01-14"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_active_window_shows_banner(self, client):
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now - 3600), "ends_at": str(now + 7200)},
        )
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "Vacation in effect" in resp.text
        assert "active" in resp.text


class TestVacationDelete:
    def test_delete_redirects(self, client):
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 100), "ends_at": str(now + 200)})
        resp = client.post("/vacation/1/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/vacation"

    def test_delete_removes_window(self, client):
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 100), "ends_at": str(now + 200)})
        client.post("/vacation/1/delete")
        resp = client.get("/vacation")
        assert "No vacation windows" in resp.text

    def test_delete_not_found_returns_404(self, client):
        resp = client.post("/vacation/9999/delete")
        assert resp.status_code == 404


class TestVacationWaterBudget:
    def _make_cluster_with_capacity(self, client, reservoir_l, flow_rate_l_per_min):
        client.post("/api/v1/clusters", json={"name": "Budget Cluster"})
        client.post(
            "/api/v1/clusters/1/irrigators",
            json={
                "tuya_device_id": "fake_budget_irr",
                "name": "Budget Pump",
                "type": "tuya_cloud",
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
        assert resp.status_code == 200
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
            "/api/v1/clusters/1/irrigators",
            json={"tuya_device_id": "fake_plain_irr", "name": "Plain Pump", "type": "tuya_cloud"},
        )
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now - 3600), "ends_at": str(now + 7 * 86400)})
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "Water budget" not in resp.text

    def test_no_budget_readout_without_any_window(self, client):
        """With capacity but no scheduled/active window, no budget readout (no crash)."""
        self._make_cluster_with_capacity(client, reservoir_l=20.0, flow_rate_l_per_min=1.0)
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "Water budget" not in resp.text

    def test_budget_readout_for_future_scheduled_window(self, client):
        """A future (scheduled, not yet active) window also drives the budget readout."""
        self._make_cluster_with_capacity(client, reservoir_l=20.0, flow_rate_l_per_min=1.0)
        now = int(time.time())
        client.post("/vacation", data={"starts_at": str(now + 86400), "ends_at": str(now + 5 * 86400)})
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "Water budget" in resp.text


class TestVacationBannerInBase:
    def test_active_vacation_shows_banner_on_other_pages(self, client):
        """Active vacation window triggers the banner in _base.html on every page."""
        now = int(time.time())
        client.post(
            "/vacation",
            data={"starts_at": str(now - 3600), "ends_at": str(now + 7200)},
        )
        # The dashboard uses _base.html — the banner should appear there too.
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Vacation mode is on" in resp.text
