# Copyright 2026 Lightibulb

"""End-to-end walk through the full EcoFac pipeline.

Raw CSV → validated readings → quality report → anomaly detection →
evidence → incident → lifecycle transitions → audit trail.

This test proves the individual modules (pipeline, quality, detection,
evidence, lifecycle) integrate correctly when exercised in sequence. Each
layer has its own focused unit/integration test elsewhere; this file is the
"do they all still compose" gate.
"""

import json

from ecofac.detection.service import analyze_at
from ecofac.incidents.lifecycle import transition_incident
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import parse_utc

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"
TARGET_TS = "2026-01-02T00:00:00Z"


def _seed_metadata(conn) -> None:
    """Minimal topology: one org, one building, one meter, one source, one batch."""
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )


def _spike_csv() -> str:
    """24 flat readings on day 1 followed by one 100-kWh spike at midnight day 2."""
    rows = [f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(24)]
    rows.append("2026-01-02T00:00:00Z,b1,m1,100.0,kWh\n")
    return HEADER + "".join(rows)


def test_full_pipeline_end_to_end():
    """Walk every stage: CSV → reading → quality → anomaly → evidence → incident."""
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_metadata(conn)

    # --- Stage 1: Ingestion ---
    summary = ingest_csv(conn, _spike_csv(), "s1", "ib1")
    assert summary.total_rows == 25
    assert summary.accepted_rows == 25
    assert summary.rejected_rows == 0

    reading_count = conn.execute("SELECT count(*) AS c FROM energy_readings").fetchone()["c"]
    assert reading_count == 25

    # --- Stage 2: Quality assessment on a clean window ---
    quality = assess_quality(
        conn,
        "m1",
        parse_utc("2026-01-01T00:00:00Z"),
        parse_utc("2026-01-01T23:00:00Z"),
    )
    assert quality.is_analysis_eligible is True
    assert quality.flags == []

    # --- Stage 3: Anomaly detection at the spike ---
    findings = analyze_at(conn, "m1", TARGET_TS)
    assert len(findings) >= 1, "At least one detector must fire on the spike"

    fresh = [f for f in findings if not f.already_recorded]
    assert fresh, "Fresh findings expected on first analysis"
    for f in fresh:
        assert not f.gated_by_quality, "Spike window must be quality-eligible"

    # Both detectors should have fired and shared one incident (find_event_incident)
    incident_ids = {f.incident_id for f in fresh if f.incident_id}
    assert len(incident_ids) == 1, "All detectors for one event share one incident"
    incident_id = incident_ids.pop()

    # --- Stage 4: Incident exists in DETECTED state ---
    inc_row = conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    assert inc_row is not None
    assert inc_row["status"] == "DETECTED"

    # --- Stage 5: Evidence is traceable back to real readings ---
    evidence_rows = conn.execute(
        "SELECT computation_log FROM evidence_records WHERE incident_id = ?",
        (incident_id,),
    ).fetchall()
    assert len(evidence_rows) == len(fresh)

    for row in evidence_rows:
        log = json.loads(row["computation_log"])
        # Top-level reading references present
        assert "reading_ids" in log
        assert len(log["reading_ids"]) >= 1
        # Every referenced ID exists in energy_readings
        for rid in log["reading_ids"]:
            found = conn.execute("SELECT 1 FROM energy_readings WHERE id = ?", (rid,)).fetchone()
            assert found is not None, f"Evidence references missing reading {rid}"
        # Detector metadata present
        assert "detector_name" in log
        assert "detector_version" in log
        assert "explanation" in log
        assert isinstance(log["explanation"], str)
        assert "observed" in log["explanation"]

    # --- Stage 6: Full lifecycle transition DETECTED → RESOLVED ---
    transition_incident(conn, incident_id, "UNDER_REVIEW")
    transition_incident(conn, incident_id, "ACKNOWLEDGED")
    transition_incident(conn, incident_id, "RESOLVED", reason="HVAC reset by ops")

    final = conn.execute(
        "SELECT status, resolution_notes FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()
    assert final["status"] == "RESOLVED"
    assert final["resolution_notes"] == "HVAC reset by ops"

    # --- Stage 7: Audit trail captures every transition ---
    audit = conn.execute(
        "SELECT action FROM audit_events WHERE entity_id = ? ORDER BY created_at",
        (incident_id,),
    ).fetchall()
    actions = [a["action"] for a in audit]
    assert "CREATED" in actions
    assert "TRANSITION_UNDER_REVIEW" in actions
    assert "TRANSITION_ACKNOWLEDGED" in actions
    assert "TRANSITION_RESOLVED" in actions
    assert len(actions) >= 4

    conn.close()


def test_full_pipeline_deterministic_on_repeat():
    """Running the same CSV twice produces the same statistical parameters."""

    def _run():
        conn = get_connection(":memory:")
        init_db(conn)
        _seed_metadata(conn)
        ingest_csv(conn, _spike_csv(), "s1", "ib1")
        findings = analyze_at(conn, "m1", TARGET_TS)
        params = [
            json.loads(
                conn.execute(
                    "SELECT detection_parameters FROM anomalies WHERE id = ?",
                    (f.anomaly_id,),
                ).fetchone()["detection_parameters"]
            )
            for f in findings
        ]
        conn.close()
        return params

    first = _run()
    second = _run()
    assert len(first) == len(second) >= 1

    # Sort by detector name so comparison is order-independent
    def _key(p: dict) -> str:
        return p.get("detector_version", "") + str(p.get("z_score", 0))

    first_sorted = sorted(first, key=_key)
    second_sorted = sorted(second, key=_key)
    for p1, p2 in zip(first_sorted, second_sorted, strict=True):
        # Everything except ephemeral fields must match
        for field in (
            "window_hours",
            "window_start",
            "window_end",
            "sample_count",
            "z_threshold",
            "z_score",
            "quality_flags",
            "detector_version",
        ):
            assert p1.get(field) == p2.get(field), f"Mismatch in {field}"
