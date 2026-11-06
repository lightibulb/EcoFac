# Copyright 2026 Lightibulb

"""Scenario-level evaluation harness.

Each scenario runs in an isolated temporary directory so the CSV and its
companion JSON files (ground_truth, quality_ground_truth, scenario_manifest)
do not clobber each other. Every run also dumps its full config into the
result so experiments can be reproduced later.
"""

import json
import pathlib
import sqlite3
import tempfile

from ecofac.detection.batch import analyze_all
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from evaluation.metrics import evaluate_detection_v2
from simulator.generate import SCENARIOS, generate_data


def _make_db() -> sqlite3.Connection:
    """Return an in-memory DB seeded with the demo topology (5 buildings x 2)."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    for b in range(1, 6):
        conn.execute(
            "INSERT INTO buildings (id, org_id, name, created_at) VALUES (?, 'o1', 'Bldg', ?)",
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
    return conn


def run_scenario(scenario: str, seed: int) -> dict:
    """Generate, ingest, analyze, evaluate one scenario. Returns a report dict."""
    if scenario not in SCENARIOS:
        return {"scenario": scenario, "seed": seed, "error": "unknown scenario"}

    conn = _make_db()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = pathlib.Path(tmpdir) / "data.csv"
            try:
                manifest = generate_data(
                    filename=str(data_path),
                    scenario=scenario,
                    seed=seed,
                )
            except Exception as exc:
                return {"scenario": scenario, "seed": seed, "error": str(exc)}

            csv_text = data_path.read_text(encoding="utf-8")
            ingest_csv(conn, csv_text, "s1", "ib1")
            analyze_all(conn)
            gt_path = pathlib.Path(manifest["files"]["ground_truth"])
            metrics = evaluate_detection_v2(conn, str(gt_path))

            return {
                "scenario": scenario,
                "seed": seed,
                "manifest": {
                    "counts": manifest["counts"],
                    "files": manifest["files"],
                },
                "metrics": metrics,
            }
    finally:
        conn.close()


def run_scenario_evaluation(
    seeds: tuple[int, ...] = (42,),
    scenarios: tuple[str, ...] | None = None,
) -> list[dict]:
    """Run each scenario at each seed. Returns list of report dicts."""
    if scenarios is None:
        scenarios = tuple(SCENARIOS)
    results: list[dict] = []
    for seed in seeds:
        results.extend(run_scenario(scenario, seed) for scenario in scenarios)
    return results


def write_report(results: list[dict], output_path: str = "scenario_report.json") -> None:
    """Persist results (deterministic: sorted keys, fixed indent)."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    report = run_scenario_evaluation(seeds=(42,))
    write_report(report)
    print(f"Scenario evaluation complete: {len(report)} runs → scenario_report.json")
