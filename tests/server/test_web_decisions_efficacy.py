"""Web decisions and efficacy tabs."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_decisions_page_renders_empty(seeded_client: TestClient) -> None:
    """Decisions page renders empty."""
    resp = seeded_client.get("/clusters/1/decisions")
    assert resp.status_code == HTTPStatus.OK
    assert "No decisions yet" in resp.text


def test_decisions_page_404(client: TestClient) -> None:
    """Decisions page 404."""
    resp = client.get("/clusters/9999/decisions")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_decisions_tab_in_cluster_tabs(seeded_client: TestClient) -> None:
    """Decisions tab in cluster tabs."""
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert 'href="/clusters/1/decisions"' in resp.text
    assert 'href="/clusters/1/efficacy"' in resp.text


def test_efficacy_page_renders_empty(seeded_client: TestClient) -> None:
    """Efficacy page renders empty."""
    resp = seeded_client.get("/clusters/1/efficacy")
    assert resp.status_code == HTTPStatus.OK
    # Either the empty state or the table heads must render
    assert "Efficacy" in resp.text or "No scored events" in resp.text


def test_efficacy_page_404(client: TestClient) -> None:
    """Efficacy page 404."""
    resp = client.get("/clusters/9999/efficacy")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_efficacy_window_segment_links(seeded_client: TestClient) -> None:
    """Efficacy window segment links."""
    resp = seeded_client.get("/clusters/1/efficacy?days=30")
    assert resp.status_code == HTTPStatus.OK
    assert "30d" in resp.text
