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


def test_scenarios_page_consumes_backend_for_all_engineering_values(client):
    """Every engineering outcome comes from the backend, not from JS math.

    The previous version of this test asserted ``"/engineering" not in html``,
    which contradicted its own name and would fail on a legitimate link.
    This version asserts the two properties that actually matter:
    (1) the page consumes the backend endpoints that pre-compute values;
    (2) the page does not recompute the primary engineering formulas.
    """
    resp = client.get("/scenarios")
    html = resp.text

    # (1) Backend endpoints the page must consume.
    for endpoint in (
        "/api/v1/scenarios/evaluate",
        "/api/v1/scenarios/compare",
        "/bottlenecks",
        "/sensitivity",
    ):
        assert endpoint in html, f"page must consume {endpoint}"

    # (2) No client-side recomputation of the primary formulas.
    # Display-only percentages for progress bars are permitted.
    for forbidden in ("* pue", "pue * ", "ev.permitted - ev.observed"):
        assert forbidden not in html, (
            f"page must not compute engineering values in JS: {forbidden!r}"
        )
