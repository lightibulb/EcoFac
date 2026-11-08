# Copyright 2026 Lightibulb
"""Route-level tests for the Virtual Facility page."""

import re


def test_world_page_renders(client):
    resp = client.get("/world")
    assert resp.status_code == 200
    assert "Virtual Facility" in resp.text


def test_world_page_has_all_containers(client):
    resp = client.get("/world")
    html = resp.text
    for element_id in (
        "viz-stage",
        "events-list",
        "action-msg",
        "drawer",
        "btn-verify-latest",
        "btn-inspect-incident",
    ):
        assert f'id="{element_id}"' in html, f"missing id={element_id}"
    # Test-drive buttons render as .preset with data-preset="<id>";
    # the old fixed IDs btn-eval-plus10/40 were removed.
    assert "data-preset" in html, (
        "world.html should render test-drive buttons with data-preset"
    )


def test_world_page_has_nav(client):
    resp = client.get("/world")
    html = resp.text
    assert 'href="/"' in html
    assert 'href="/decisions"' in html
    assert 'href="/scenarios"' in html
    assert 'href="/world"' in html


def test_world_page_references_world_endpoint(client):
    resp = client.get("/world")
    assert "/api/v1/world/state" in resp.text


def test_world_page_has_disclaimer(client):
    resp = client.get("/world")
    html = resp.text
    assert "Synthetic data" in html


def test_world_page_has_legend(client):
    resp = client.get("/world")
    html = resp.text
    assert "No open incidents" in html
    assert "Has severe incident" in html
    assert "No valid reading" in html


def test_world_page_no_unrendered_jinja(client):
    resp = client.get("/world")
    html = resp.text
    assert "{{" not in html
    assert "{%" not in html


def test_world_page_no_hardcoded_uuid(client):
    resp = client.get("/world")
    uuids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        resp.text,
    )
    assert uuids == [], f"template hardcodes UUIDs: {uuids}"


def test_world_page_has_reduced_motion_support(client):
    resp = client.get("/world")
    assert "prefers-reduced-motion" in resp.text


def test_world_page_two_views(client):
    resp = client.get("/world")
    html = resp.text
    assert 'data-view="datacenter"' in html
    assert 'data-view="building"' in html


def test_nav_links_to_world_from_all_pages(client):
    for path in ("/", "/decisions", "/scenarios", "/world"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert 'href="/world"' in resp.text, f"{path} missing /world link"


def test_world_in_openapi(client):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/world/state" in schema["paths"]
    assert "/world" in schema["paths"]
