# Copyright 2026 Lightibulb
"""The simulator's labels must be fair and its defect counts must be published."""
import csv
import json
import random

from simulator.generate import DETECTION_WINDOW, generate_data


def test_ground_truth_is_reconciled_and_quality_truth_written(tmp_path):
    out = tmp_path / "data.csv"
    generate_data(filename=str(out), days=10, buildings=2, anomaly_prob=0.05)

    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    present = {(r["meter_id"], r["timestamp"]) for r in rows}
    truth = json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))

    assert truth, "expected some labelled spikes"
    for label in truth:
        assert (label["meter_id"], label["timestamp"]) in present
        earlier = [t for (m, t) in present if m == label["meter_id"] and t < label["timestamp"]]
        assert len(earlier) >= DETECTION_WINDOW

    quality = json.loads((tmp_path / "quality_ground_truth.json").read_text(encoding="utf-8"))
    assert set(quality) == {"duplicates", "missing", "stale_readings"}
    assert quality["duplicates"] > 0


def test_generation_is_reproducible_and_leaves_global_rng_alone(tmp_path):
    random.seed(123)
    expected = random.random()
    random.seed(123)
    generate_data(filename=str(tmp_path / "a.csv"), days=3, buildings=1)
    assert random.random() == expected  # generator did not touch the global RNG
    generate_data(filename=str(tmp_path / "b.csv"), days=3, buildings=1)
    assert (tmp_path / "a.csv").read_text() == (tmp_path / "b.csv").read_text()
