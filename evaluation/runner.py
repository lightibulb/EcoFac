# Copyright 2026 Lightibulb

"""Orchestrates data generation, ingestion, detection, and reporting."""

import json
import time

from evaluation.metrics import evaluate_detection, evaluate_quality
from simulator.generate import generate_data

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db


def run_evaluation() -> None:
    """Run the full end-to-end evaluation flow."""
    print("Starting EcoFac Evaluation...")

    # 1. Generate Data
    start_time = time.time()
    generate_data()
    gen_time = time.time() - start_time
    print(f"Data generated in {gen_time:.2f}s")

    # 2. Setup DB
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"

    # Seed metadata
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,)
    )
    for b in range(1, 6):
        conn.execute(
            "INSERT INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o1', 'Bldg', ?)",
            (f"b{b}", now)
        )
        for m in range(1, 3):
            conn.execute(
                "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
                "VALUES (?, ?, 'elec', 'kWh', ?)",
                (f"m{b}-{m}", f"b{b}", now)
            )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,)
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,)
    )

    # 3. Ingest Data
    start_time = time.time()
    with open("synthetic_data.csv", encoding="utf-8") as f:
        csv_content = f.read()

    summary = ingest_csv(conn, csv_content, "s1", "ib1")
    ingest_time = time.time() - start_time
    print(f"Ingested {summary.total_rows} rows in {ingest_time:.2f}s")

    # 4. Analyze Data
    start_time = time.time()
    cursor = conn.execute(
        "SELECT id, timestamp_utc FROM energy_readings WHERE is_valid = 1"
    )
    readings = cursor.fetchall()

    detected_count = 0
    for r in readings:
        # Run detectors
        a1 = detect_rolling_baseline(conn, r["id"], r["timestamp_utc"])
        a2 = detect_robust_mad(conn, r["id"], r["timestamp_utc"])

        for a in [a1, a2]:
            if a:
                create_incident(conn, a)
                detected_count += 1

    analyze_time = time.time() - start_time
    print(f"Analyzed {len(readings)} readings in {analyze_time:.2f}s")

    # 5. Evaluate
    detection_metrics = evaluate_detection(conn, "ground_truth.json")
    quality_metrics = evaluate_quality(conn)

    report = {
        "system_performance": {
            "generation_time_sec": round(gen_time, 2),
            "ingestion_time_sec": round(ingest_time, 2),
            "analysis_time_sec": round(analyze_time, 2),
        },
        "ingestion_summary": {
            "total_rows": summary.total_rows,
            "accepted": summary.accepted_rows,
            "duplicates": summary.duplicate_rows,
            "missing_removed": summary.total_rows - summary.accepted_rows
        },
        "detection_metrics": detection_metrics,
        "quality_metrics": quality_metrics,
    }

    with open("evaluation_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Human readable summary
    with open("evaluation_summary.md", "w", encoding="utf-8") as f:
        f.write("# EcoFac Evaluation Summary\n\n")
        f.write("## Performance\n")
        f.write(f"- Ingestion Time: {ingest_time:.2f}s\n")
        f.write(f"- Analysis Time: {analyze_time:.2f}s\n\n")
        f.write("## Detection Metrics\n")
        f.write(f"- Precision: {detection_metrics['precision']}\n")
        f.write(f"- Recall: {detection_metrics['recall']}\n")
        f.write(f"- F1 Score: {detection_metrics['f1']}\n")
        f.write(
            f"- False Alerts / Bldg / Week: "
            f"{detection_metrics['false_alerts_per_bldg_week']}\n\n"
        )
        f.write("## Limitations\n")
        f.write("- Synthetic data only.\n")
        f.write("- Thresholds tuned on this specific synthetic seed.\n")

    print("Evaluation complete. See evaluation_report.json and evaluation_summary.md")


if __name__ == "__main__":
    run_evaluation()
