# Copyright 2026 Lightibulb
"""Route-level tests for the Decision Center page and nav link."""


def test_decisions_page_renders(client):
    resp = client.get("/decisions")
    assert resp.status_code == 200
    assert "Decision Center" in resp.text
    assert 'id="decision-center"' in resp.text


def test_decisions_page_has_nav(client):
    resp = client.get("/decisions")
    assert 'href="/"' in resp.text
    assert 'href="/decisions"' in resp.text


def test_decisions_page_disclaimer_present(client):
    resp = client.get("/decisions")
    # The page must make it explicit that decisions are advisory only.
    assert "Advisory only" in resp.text
    assert "does not control physical equipment" in resp.text


def test_decisions_page_loads_without_errors(client):
    """If backend schemas drift, /decisions would fail to render its shell."""
    resp = client.get("/decisions")
    assert resp.status_code == 200
    # No unrendered Jinja placeholders
    assert "{{" not in resp.text
    assert "{%" not in resp.text


def test_index_page_has_decisions_link(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/decisions"' in resp.text


def test_decisions_page_references_expected_endpoints(client):
    resp = client.get("/decisions")
    # The page's JS must call the read-only decision endpoints we expose.
    assert "/api/v1/decisions/stats" in resp.text
    assert "/api/v1/decisions/" in resp.text
    assert "/verify" in resp.text
    assert "/engineering" in resp.text
