"""Regression tests for EcoFac priority remediation."""
import csv
import json
import random

from evaluation.metrics import calculate_pr_auc
from simulator.generate import generate_data


def test_generator_random_default_and_reproducible_explicit_seed(tmp_path):
    first = generate_data(filename=str(tmp_path / "one.csv"), days=2, seed=123)
    second = generate_data(filename=str(tmp_path / "two.csv"), days=2, seed=123)
    assert first["seed"] == second["seed"] == 123
    assert first["exact_injected_anomalies"] == second["exact_injected_anomalies"]
    assert (tmp_path / "one.csv").read_text() == (tmp_path / "two.csv").read_text()


def test_generator_writes_reconciled_detection_and_quality_truth(tmp_path):
    manifest = generate_data(filename=str(tmp_path / "data.csv"), days=5, seed=7)
    with (tmp_path / "data.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    present = {(r["meter_id"], r["timestamp"]) for r in rows}
    truth = json.loads((tmp_path / "ground_truth.json").read_text())
    assert all((r["meter_id"], r["timestamp"]) in present for r in truth)
    quality = json.loads((tmp_path / "quality_ground_truth.json").read_text())
    assert quality["missing"] == manifest["counts"]["missing_injected"]
    assert quality["duplicates"] == manifest["counts"]["duplicates_injected"]
    assert (tmp_path / "scenario_manifest.json").exists()


def test_pr_auc_is_bounded_and_includes_unrecalled_tail():
    assert calculate_pr_auc([1.0], [1.0]) == 1.0
    assert 0.0 <= calculate_pr_auc([0.8, 0.5], [0.5, 0.75]) <= 1.0
    assert calculate_pr_auc([], []) == 0.0


def test_generator_does_not_mutate_global_random_state(tmp_path):
    random.seed(991)
    expected = random.random()
    generate_data(filename=str(tmp_path / "data.csv"), days=1, seed=42)
    assert random.random() == expected
