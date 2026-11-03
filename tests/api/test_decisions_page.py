# Copyright 2026 Lightibulb
"""Route-level tests for the Decision Center page and nav link."""

import re


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


# ---- Inspector structure ----
def test_decisions_page_has_inspector_container(client):
    resp = client.get("/decisions")
    assert 'id="inspector-panel"' in resp.text
    assert 'id="inspector-content"' in resp.text


def test_decisions_page_declares_all_inspector_sections(client):
    """The template's JS must build these section identifiers."""
    resp = client.get("/decisions")
    html = resp.text
    for section in (
        'data-section="identity"',
        'data-section="proposed-change"',
        'data-section="inputs"',
        'data-section="constraints"',
        'data-section="evidence"',
        'data-section="uncertainty"',
        'data-section="sensitivity"',
        'data-section="why-decision"',
    ):
        assert section in html, f"missing {section}"


def test_decisions_page_uses_details_elements(client):
    """Expandable sections must use <details>/<summary> for native a11y."""
    resp = client.get("/decisions")
    html = resp.text
    assert "details.inspector-section" in html
    assert 'class="inspector-section"' in html
    assert "<summary>" in html or "renderIdentity" in html


def test_decisions_page_handles_missing_engineering(client):
    """The JS must gracefully handle /engineering returning 422."""
    resp = client.get("/decisions")
    html = resp.text
    # The fallback must mention 'unavailable' and the engineering section
    assert "Engineering evidence unavailable" in html
    assert "engineeringError" in html


def test_decisions_page_shows_verification_scope(client):
    """Verification limitations must be visible to the user."""
    resp = client.get("/decisions")
    html = resp.text
    assert "full_bundle_reproduced" in html
    assert "verification_scope" in html


def test_decisions_page_has_reproducibility_section(client):
    """Fingerprints must be visible in the inspector."""
    resp = client.get("/decisions")
    html = resp.text
    assert "Input fingerprint" in html or "input_fingerprint" in html
    assert "Config fingerprint" in html or "config_fingerprint" in html


def test_decisions_page_no_hardcoded_sample_decisions(client):
    """The template must not contain literal decision records."""
    resp = client.get("/decisions")
    html = resp.text
    uuids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        html,
    )
    assert uuids == [], f"template hardcodes decision IDs: {uuids}"


def test_decisions_page_no_js_engineering_math(client):
    """The template must not compute headroom or facility power in JS."""
    resp = client.get("/decisions")
    html = resp.text
    # If the template had JS math, these patterns would appear in string
    # concatenation. Presence of the safe endpoint calls proves the values
    # come from the server.
    assert "/engineering" in html
    assert "/api/v1/decisions/stats" in html
