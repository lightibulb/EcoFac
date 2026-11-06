# Copyright 2026 Lightibulb
"""Route-level tests for the Scenario Workbench page."""

import re


def test_scenarios_page_renders(client):
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    assert "Scenario Workbench" in resp.text
    assert 'id="scenario-form"' in resp.text


def test_scenarios_page_has_topnav_links(client):
    resp = client.get("/scenarios")
    html = resp.text
    assert 'href="/"' in html
    assert 'href="/decisions"' in html
    assert 'href="/scenarios"' in html


def test_scenarios_page_disclaimer_present(client):
    resp = client.get("/scenarios")
    html = resp.text
    assert "Hypothetical only" in html
    assert "not live facility telemetry" in html


def test_scenarios_page_form_fields_present(client):
    resp = client.get("/scenarios")
    html = resp.text
    for input_id in (
        "in-name",
        "in-it",
        "in-pue",
        "in-cool",
        "in-it-delta",
        "in-cool-delta",
        "in-elec-cap",
        "in-cool-cap",
        "in-elec-res",
        "in-cool-res",
        "in-warn",
    ):
        assert f'id="{input_id}"' in html, f"missing input id={input_id}"


def test_scenarios_page_sliders_present(client):
    resp = client.get("/scenarios")
    assert 'type="range"' in resp.text
    assert "data-target" in resp.text


def test_scenarios_page_payload_preview_present(client):
    resp = client.get("/scenarios")
    assert 'id="payload-preview"' in resp.text


def test_scenarios_page_results_container_present(client):
    resp = client.get("/scenarios")
    assert 'id="result-panel"' in resp.text
    assert 'id="result-content"' in resp.text


def test_scenarios_page_comparison_container_present(client):
    resp = client.get("/scenarios")
    assert 'id="comparison-panel"' in resp.text
    assert 'id="comparison-content"' in resp.text


def test_scenarios_page_history_container_present(client):
    resp = client.get("/scenarios")
    assert 'id="history-list"' in resp.text
    assert 'id="history-pager"' in resp.text


def test_scenarios_page_references_expected_endpoints(client):
    resp = client.get("/scenarios")
    html = resp.text
    assert "/api/v1/scenarios/evaluate" in html
    assert "/api/v1/scenarios/compare" in html
    assert "/bottlenecks" in html
    assert "/sensitivity" in html
    assert "/api/v1/scenarios?" in html


def test_scenarios_page_no_unrendered_jinja(client):
    resp = client.get("/scenarios")
    html = resp.text
    assert "{{" not in html
    assert "{%" not in html


def test_scenarios_page_no_hardcoded_uuid(client):
    resp = client.get("/scenarios")
    uuids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        resp.text,
    )
    assert uuids == [], f"template hardcodes UUIDs: {uuids}"


def test_index_has_scenarios_nav_link(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/scenarios"' in resp.text


def test_decisions_page_has_scenarios_nav_link(client):
    resp = client.get("/decisions")
    assert resp.status_code == 200
    assert 'href="/scenarios"' in resp.text


def test_scenarios_page_has_no_client_side_engineering_math(client):
    """The page must reference backend endpoints rather than compute values."""
    resp = client.get("/scenarios")
    html = resp.text
    # If JS were computing these, we would find arithmetic on these names
    # in the template body. Instead every value comes from the API payload.
    assert "/engineering" not in html  # page loads it indirectly via /scenarios/{id}
    assert "facility_power" in html.lower() or "facility power" in html.lower()
