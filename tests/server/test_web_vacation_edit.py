"""Web UI tests for /vacation/{id}/edit (GET form + POST update)."""

import time
from http import HTTPStatus


def _create_window(client, *, starts_offset=3600, ends_offset=7200):
    now = int(time.time())
    resp = client.post(
        "/vacation",
        data={"starts_at": str(now + starts_offset), "ends_at": str(now + ends_offset)},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    return now


class TestVacationEditPage:
    """Tests for vacation edit page."""

    def test_renders_form_with_existing_values(self, client):
        """Renders form with existing values."""
        _create_window(client)
        resp = client.get("/vacation/1/edit")
        assert resp.status_code == HTTPStatus.OK
        assert "Edit vacation" in resp.text
        assert 'name="starts_at"' in resp.text
        assert 'name="ends_at"' in resp.text
        assert 'name="contact_email"' in resp.text
        assert 'name="notes"' in resp.text

    def test_edit_link_appears_on_list(self, client):
        """Edit link appears on list."""
        _create_window(client)
        resp = client.get("/vacation")
        assert resp.status_code == HTTPStatus.OK
        assert "/vacation/1/edit" in resp.text

    def test_inputs_have_matching_labels_a11y(self, client):
        """Inputs have matching labels a11y."""
        _create_window(client)
        resp = client.get("/vacation/1/edit")
        assert resp.status_code == HTTPStatus.OK
        for field in ("starts_at", "ends_at", "contact_email", "notes"):
            assert f'for="{field}"' in resp.text
            assert f'id="{field}"' in resp.text

    def test_unknown_window_returns_404(self, client):
        """Unknown window returns 404."""
        resp = client.get("/vacation/9999/edit")
        assert resp.status_code == HTTPStatus.NOT_FOUND


class TestVacationUpdate:
    """Tests for vacation update."""

    def test_update_persists_and_redirects(self, client):
        """Update persists and redirects."""
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
        assert resp.status_code == HTTPStatus.SEE_OTHER
        assert resp.headers["location"] == "/vacation"

        listed = client.get("/vacation")
        assert listed.status_code == HTTPStatus.OK
        assert "neighbor@example.com" in listed.text
        assert "Watering by hand" in listed.text

    def test_update_iso_dates_round_trip(self, client):
        """Update ISO dates round trip."""
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "2030-06-01", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.SEE_OTHER

        # Edit page should re-populate the dates as YYYY-MM-DD via format_ts.
        form = client.get("/vacation/1/edit")
        assert "2030-06-01" in form.text
        assert "2030-06-10" in form.text

    def test_update_rejects_end_before_start(self, client):
        """Update rejects end before start."""
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "2030-06-10", "ends_at": "2030-06-01"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.BAD_REQUEST

    def test_update_rejects_bad_date(self, client):
        """Update rejects bad date."""
        _create_window(client)
        resp = client.post(
            "/vacation/1/edit",
            data={"starts_at": "not-a-date", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.BAD_REQUEST

    def test_update_unknown_window_returns_404(self, client):
        """Update unknown window returns 404."""
        resp = client.post(
            "/vacation/9999/edit",
            data={"starts_at": "2030-06-01", "ends_at": "2030-06-10"},
            follow_redirects=False,
        )
        assert resp.status_code == HTTPStatus.NOT_FOUND
