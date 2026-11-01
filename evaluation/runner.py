# Copyright 2026 Lightibulb

"""Orchestrates data generation, ingestion, detection, and reporting."""

import dataclasses
import datetime as dt
import json
import os
import sqlite3
import time
import uuid

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from ecofac.timeutil import now_z
from evaluation.metrics import evaluate_detection, evaluate_quality
from evaluation.verification import verify_intervention
from simulator.generate import generate_data


def seed_metadata(conn: sqlite3.Connection, now: str = "2026-01-01T00:00:00Z") -> None:
    """Seed organizations, buildings, meters, source, and batch metadata."""
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    for b in range(1, 6):
        conn.execute(
            "INSERT INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o1', 'Bldg', ?)",
            (f"b{b}", now),
        )
        for m in range(1, 3):
            conn.execute(
                "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
                "VALUES (?, ?, 'elec', 'kWh', ?)",
                (f"m{b}-{m}", f"b{b}", now),
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


def analyze_all(conn: sqlite3.Connection, *, create_incidents: bool = True) -> int:
    """Run both detectors over every valid reading.

    Passes the METER ID (not the reading id) to the detectors. Incidents are
    created only for anomalies whose data-quality window is eligible.
    Returns the number of anomalies detected.
    """
    cursor = conn.execute(
        "SELECT id, meter_id, timestamp_utc FROM energy_readings "
        "WHERE is_valid = 1 ORDER BY timestamp_utc"
    )
    readings = cursor.fetchall()
    detected = 0
    for r in readings:
        anomalies = [
            detect_rolling_baseline(conn, r["meter_id"], r["timestamp_utc"]),
            detect_robust_mad(conn, r["meter_id"], r["timestamp_utc"]),
        ]
        for anomaly in anomalies:
            if anomaly is None:
                continue
            detected += 1
            if create_incidents and anomaly.is_data_quality_eligible:
                create_incident(conn, anomaly)
    return detected


def run_evaluation() -> None:
    """Run the full end-to-end evaluation flow."""
    print("Starting EcoFac Evaluation...")

    # 1. Generate data
    start_time = time.time()
    seed_raw = os.environ.get("ECOFAC_SEED")
    scenario_manifest = generate_data(seed=int(seed_raw) if seed_raw else None)
    gen_time = time.time() - start_time
    print(f"Data generated in {gen_time:.2f}s")

    # 2. Setup DB
    conn = get_connection(":memory:")
    init_db(conn)
    seed_metadata(conn)

    # 3. Ingest data
    start_time = time.time()
    with open("synthetic_data.csv", encoding="utf-8") as f:
        csv_content = f.read()
    summary = ingest_csv(conn, csv_content, "s1", "ib1")
    ingest_time = time.time() - start_time
    print(f"Ingested {summary.total_rows} rows in {ingest_time:.2f}s")

    # 4. Analyze data
    start_time = time.time()
    detected_count = analyze_all(conn)
    analyze_time = time.time() - start_time
    print(f"Analysis complete: {detected_count} anomalies in {analyze_time:.2f}s")

    # 5. Evaluate
    detection_metrics = evaluate_detection(conn, "ground_truth.json")
    quality_metrics = evaluate_quality(conn)

    # Exit guard: a blind evaluation must fail loudly, never publish zeros.
    if detection_metrics["true_positives"] == 0:
        msg = (
            "FATAL: zero ground-truth anomalies were detected. "
            "The evaluation pipeline is blind; refusing to publish a report."
        )
        raise SystemExit(msg)

    # 6. Verification: simulate a successful intervention on meter m1-1
    #    (30% consumption cut from 2026-03-18 onward, and the remediated
    #    fault no longer produces spikes in the post window).
    intervention_date = dt.datetime(2026, 3, 17, tzinfo=dt.UTC)
    conn.execute(
        "UPDATE energy_readings SET value = value * 0.7 "
        "WHERE meter_id = 'm1-1' AND timestamp_utc >= '2026-03-18T00:00:00Z'"
    )
    with open("ground_truth.json", encoding="utf-8") as f:
        post_spike_ts = [
            t["timestamp"]
            for t in json.load(f)
            if t["meter_id"] == "m1-1" and t["timestamp"] >= "2026-03-18T00:00:00Z"
        ]
    if post_spike_ts:
        placeholders = ",".join("?" * len(post_spike_ts))
        conn.execute(
            "DELETE FROM energy_readings WHERE meter_id = 'm1-1' "
            f"AND timestamp_utc IN ({placeholders})",
            post_spike_ts,
        )
    v_result = verify_intervention(conn, "m1-1", intervention_date)
    incident_row = conn.execute("SELECT id FROM incidents LIMIT 1").fetchone()
    if incident_row:
        conn.execute(
            "INSERT INTO verification_runs (id, incident_id, result, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                incident_row["id"],
                json.dumps(dataclasses.asdict(v_result)),
                now_z(),
            ),
        )
    print(
        f"Verification: {v_result.status} "
        f"({v_result.percent_change:+.1f}% change, "
        f"{v_result.data_completeness_pct}% complete)"
    )

    # 7. Report
    report = {
        "scenario": {
            "seed": scenario_manifest["seed"],
            "seed_was_explicit": scenario_manifest["seed_was_explicit"],
            "config": scenario_manifest["config"],
            "counts": scenario_manifest["counts"],
            "manifest_file": "scenario_manifest.json",
        },
        "system_performance": {
            "generation_time_sec": round(gen_time, 2),
            "ingestion_time_sec": round(ingest_time, 2),
            "analysis_time_sec": round(analyze_time, 2),
        },
        "ingestion_summary": {
            "total_rows": summary.total_rows,
            "accepted": summary.accepted_rows,
            "rejected": summary.rejected_rows,
            "duplicates": summary.duplicate_rows,
        },
        "detection_metrics": detection_metrics,
        "quality_metrics": quality_metrics,
        "verification": {
            "meter_id": "m1-1",
            "status": v_result.status,
            "percent_change": v_result.percent_change,
            "data_completeness_pct": v_result.data_completeness_pct,
            "caveats": v_result.caveats,
        },
    }
    with open("evaluation_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    with open("evaluation_summary.md", "w", encoding="utf-8") as f:
        f.write("# EcoFac Evaluation Summary\n\n")
        f.write("## Performance\n")
        f.write(f"- Ingestion Time: {ingest_time:.2f}s\n")
        f.write(f"- Analysis Time: {analyze_time:.2f}s\n\n")
        f.write("## Detection Metrics\n")
        f.write(f"- Precision: {detection_metrics['precision']}\n")
        f.write(f"- Recall: {detection_metrics['recall']}\n")
        f.write(f"- F1 Score: {detection_metrics['f1']}\n")
        f.write(f"- PR-AUC: {detection_metrics['pr_auc']}\n")
        f.write(
            "- False Alerts / Bldg / Week: "
            f"{detection_metrics['false_alerts_per_bldg_week']}\n\n"
        )
        f.write("## Verification\n")
        f.write("- Meter: m1-1 (synthetic 30% reduction intervention)\n")
        f.write(f"- Outcome: {v_result.status}\n")
        f.write(f"- Change: {v_result.percent_change:+.1f}%\n")
        f.write(f"- Completeness: {v_result.data_completeness_pct}%\n\n")
        f.write("## Limitations\n")
        f.write("- Synthetic data only.\n")
        f.write("- Thresholds tuned on this specific synthetic seed.\n")

    print("Evaluation complete. See evaluation_report.json and evaluation_summary.md")


if __name__ == "__main__":
    run_evaluation()
