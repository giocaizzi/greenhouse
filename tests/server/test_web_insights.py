"""Web learn page — structured insight cards (replaces <pre> dump)."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_learn_page_renders_insight_cards(seeded_client: TestClient) -> None:
    """Learn page renders insight cards and the forecast, not the raw report dump."""
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == HTTPStatus.OK
    assert "Insights" in resp.text
    # Structured card markup must be present
    assert 'class="insight' in resp.text
    # The raw <pre class="report"> text-dump must be gone
    assert 'class="report"' not in resp.text
    # Forecast section heading must be present
    assert "Forecast" in resp.text


def test_learn_page_404_for_missing_cluster(client: TestClient) -> None:
    """Learn page 404 for missing cluster."""
    resp = client.get("/clusters/9999/learn")
    assert resp.status_code == HTTPStatus.NOT_FOUND
