"""Web UI tests for the cluster watering-schedule section and window CRUD."""


class TestConfigPageWindowsSection:
    def test_renders_empty_state_when_no_windows(self, seeded_client):
        resp = seeded_client.get("/clusters/1/config")
        assert resp.status_code == 200
        assert "Watering schedule" in resp.text
        assert "No windows configured" in resp.text

    def test_add_form_has_all_labelled_fields(self, seeded_client):
        resp = seeded_client.get("/clusters/1/config")
        assert resp.status_code == 200
        # a11y: every input has matching for/id
        for field in ("new_start_hour", "new_end_hour", "new_label"):
            assert f'for="{field}"' in resp.text
            assert f'id="{field}"' in resp.text
        # weekday checkboxes (1..64)
        for bit in (1, 2, 4, 8, 16, 32, 64):
            assert f'id="new_dow_{bit}"' in resp.text
            assert f'value="{bit}"' in resp.text


class TestCreateWindow:
    def test_create_persists_and_redirects(self, seeded_client):
        resp = seeded_client.post(
            "/clusters/1/windows",
            data={
                "start_hour": "6",
                "end_hour": "10",
                "weekday_mask": ["1", "2", "4", "8", "16"],  # Mon..Fri
                "label": "Workday AM",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/clusters/1/config"

        page = seeded_client.get("/clusters/1/config")
        assert page.status_code == 200
        assert "Workday AM" in page.text
        assert "06:00–10:00" in page.text
        # 31 = Mon..Fri mask → rendered as weekday labels
        assert "Mon" in page.text
        assert "Fri" in page.text

    def test_create_renders_every_day_when_full_mask(self, seeded_client):
        seeded_client.post(
            "/clusters/1/windows",
            data={
                "start_hour": "7",
                "end_hour": "9",
                "weekday_mask": ["1", "2", "4", "8", "16", "32", "64"],
            },
        )
        page = seeded_client.get("/clusters/1/config")
        assert "Every day" in page.text

    def test_create_rejects_equal_hours(self, seeded_client):
        resp = seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "8", "end_hour": "8", "weekday_mask": ["1"]},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_create_rejects_no_weekdays(self, seeded_client):
        resp = seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": []},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_create_rejects_invalid_weekday_value(self, seeded_client):
        resp = seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["3"]},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_create_404_for_unknown_cluster(self, client):
        resp = client.post(
            "/clusters/999/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"]},
            follow_redirects=False,
        )
        assert resp.status_code == 404


class TestEditWindow:
    def _seed_window(self, seeded_client):
        seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"], "label": "AM"},
        )

    def test_edit_form_renders_with_values(self, seeded_client):
        self._seed_window(seeded_client)
        resp = seeded_client.get("/clusters/1/windows/1/edit")
        assert resp.status_code == 200
        assert "Edit watering window" in resp.text
        assert 'value="6"' in resp.text
        assert 'value="10"' in resp.text
        assert 'value="AM"' in resp.text
        # the Mon checkbox (bit 1) should be checked
        assert 'id="dow_1"' in resp.text and "checked" in resp.text

    def test_edit_inputs_have_matching_labels_a11y(self, seeded_client):
        self._seed_window(seeded_client)
        resp = seeded_client.get("/clusters/1/windows/1/edit")
        assert resp.status_code == 200
        for field in ("start_hour", "end_hour", "label"):
            assert f'for="{field}"' in resp.text
            assert f'id="{field}"' in resp.text

    def test_update_persists(self, seeded_client):
        self._seed_window(seeded_client)
        resp = seeded_client.post(
            "/clusters/1/windows/1/edit",
            data={
                "start_hour": "18",
                "end_hour": "20",
                "weekday_mask": ["32", "64"],  # Sat + Sun
                "label": "Weekend PM",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/clusters/1/config"

        page = seeded_client.get("/clusters/1/config")
        assert "Weekend PM" in page.text
        assert "18:00–20:00" in page.text

    def test_update_404_unknown_window(self, seeded_client):
        resp = seeded_client.post(
            "/clusters/1/windows/9999/edit",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"]},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_update_404_cross_cluster(self, seeded_client):
        """Window from cluster 1 cannot be edited via cluster 2's URL."""
        self._seed_window(seeded_client)
        # Create a second cluster.
        resp = seeded_client.post(
            "/api/v1/clusters",
            json={"name": "Second", "environment": "indoor"},
        )
        assert resp.status_code == 201
        cid2 = resp.json()["id"]
        # Try to edit window 1 (belongs to cluster 1) via cluster 2 URL.
        resp = seeded_client.post(
            f"/clusters/{cid2}/windows/1/edit",
            data={"start_hour": "5", "end_hour": "7", "weekday_mask": ["1"]},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_edit_form_404_cross_cluster(self, seeded_client):
        self._seed_window(seeded_client)
        resp = seeded_client.post(
            "/api/v1/clusters",
            json={"name": "Second", "environment": "indoor"},
        )
        cid2 = resp.json()["id"]
        resp = seeded_client.get(f"/clusters/{cid2}/windows/1/edit")
        assert resp.status_code == 404


class TestDeleteWindow:
    def test_delete_returns_empty_body(self, seeded_client):
        seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"]},
        )
        resp = seeded_client.delete("/clusters/1/windows/1")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_delete_removes_row(self, seeded_client):
        seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"], "label": "Gone"},
        )
        seeded_client.delete("/clusters/1/windows/1")
        page = seeded_client.get("/clusters/1/config")
        assert "Gone" not in page.text
        assert "No windows configured" in page.text

    def test_delete_404_unknown_window(self, seeded_client):
        resp = seeded_client.delete("/clusters/1/windows/9999")
        assert resp.status_code == 404

    def test_delete_404_cross_cluster(self, seeded_client):
        seeded_client.post(
            "/clusters/1/windows",
            data={"start_hour": "6", "end_hour": "10", "weekday_mask": ["1"]},
        )
        resp = seeded_client.post(
            "/api/v1/clusters",
            json={"name": "Second", "environment": "indoor"},
        )
        cid2 = resp.json()["id"]
        resp = seeded_client.delete(f"/clusters/{cid2}/windows/1")
        assert resp.status_code == 404
