"""Primary navigation: More overflow menu, footer health link, bell anchor."""


def test_more_menu_links_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # "More" menu items
    assert 'href="/vacation"' in resp.text
    assert 'href="/health"' in resp.text
    assert 'href="/quality"' in resp.text
    assert 'href="/preferences"' in resp.text


def test_bell_is_anchor_not_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # The bell should be a real link to /alerts, not a button with inline onclick
    assert "window.location.href='/alerts'" not in resp.text
    assert 'href="/alerts"' in resp.text


def test_footer_health_badge_links_to_health_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Health badge wrapper is now an anchor pointing at /health
    assert 'id="health-badge"' in resp.text
    assert 'href="/health"' in resp.text
