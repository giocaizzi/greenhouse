"""Web UI tests for /vacation/{id}/edit (GET form + POST update)."""

import time


def _create_window(client, *, starts_offset=3600, ends_offset=7200):
    now = int(time.time())
    resp = client.post(
        "/vacation",
        data={"starts_at": str(now + starts_offset), "ends_at": str(now + ends_offset)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return now


class TestVacationEditPage:
    def test_renders_form_with_existing_values(self, client):
        _create_window(client)
        resp = client.get("/vacation/1/edit")
        assert resp.status_code == 200
        assert "Edit vacation" in resp.text
        assert 'name="starts_at"' in resp.text
        assert 'name="ends_at"' in resp.text
        assert 'name="contact_email"' in resp.text
        assert 'name="notes"' in resp.text

    def test_edit_link_appears_on_list(self, client):
        _create_window(client)
        resp = client.get("/vacation")
        assert resp.status_code == 200
        assert "/vacation/1/edit" in resp.text

    def test_inputs_have_matching_labels_a11y(self, client):
        _create_window(client)
        resp = client.get("/vacation/1/edit")
        assert resp.status_code == 200
        for field in ("starts_at", "ends_at", "contact_email", "notes"):
            assert f'for="{field}"' in resp.text
            assert f'id="{field}"' in resp.text

    def test_unknown_window_returns_404(self, client):
        resp = client.get("/vacation/9999/edit")
        assert resp.status_code == 404


class TestVacationUpdate:
    def test_update_persists_and_redirects(self, client):
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={
                "starts_at": "2030-06-01",
                "ends_at": "2030-06-15",
                "contact_email": "neighbor@example.com",
                "notes": "Watering by hand",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/vacation"

        listed = client.get("/vacation")
        assert listed.status_code == 200
        assert "neighbor@example.com" in listed.text
        assert "Watering by hand" in listed.text

    def test_update_iso_dates_round_trip(self, client):
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "2030-06-01", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Edit page should re-populate the dates as YYYY-MM-DD via format_ts.
        form = client.get("/vacation/1/edit")
        assert "2030-06-01" in form.text
        assert "2030-06-10" in form.text

    def test_update_rejects_end_before_start(self, client):
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "2030-06-10", "ends_at": "2030-06-01"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_update_rejects_bad_date(self, client):
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "not-a-date", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_update_unknown_window_returns_404(self, client):
        resp = client.post(
            "/vacation/9999/edit",
            data={"starts_at": "2030-06-01", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
