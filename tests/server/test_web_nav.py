"""Primary navigation: More overflow menu, footer health link, bell anchor."""

from http import HTTPStatus


def test_more_menu_links_present(client):
    """More menu links present."""
    resp = client.get("/")
    assert resp.status_code == HTTPStatus.OK
    # "More" menu items
    assert 'href="/vacation"' in resp.text
    assert 'href="/health"' in resp.text
    assert 'href="/quality"' in resp.text
    assert 'href="/preferences"' in resp.text


def test_bell_is_anchor_not_button(client):
    """Bell is anchor not button."""
    resp = client.get("/")
    assert resp.status_code == HTTPStatus.OK
    # The bell should be a real link to /alerts, not a button with inline onclick
    assert "window.location.href='/alerts'" not in resp.text
    assert 'href="/alerts"' in resp.text


def test_footer_health_badge_links_to_health_page(client):
    """Footer health badge links to health page."""
    resp = client.get("/")
    assert resp.status_code == HTTPStatus.OK
    # Health badge wrapper is now an anchor pointing at /health
    assert 'id="health-badge"' in resp.text
    assert 'href="/health"' in resp.text
