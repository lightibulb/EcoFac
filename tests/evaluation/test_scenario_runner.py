# Copyright 2026 Lightibulb

"""End-to-end smoke test for the scenario evaluation harness."""

import json

from evaluation.scenario_eval import run_scenario_evaluation, write_report


def test_scenario_runner_smoke():
    results = run_scenario_evaluation(seeds=(42,), scenarios=("classic", "noise"))
    assert len(results) == 2
    for r in results:
        assert r["scenario"] in {"classic", "noise"}
        assert "metrics" in r
        metrics = r["metrics"]
        for key in (
            "precision",
            "recall",
            "f1",
            "false_positive_rate",
            "true_positives",
            "false_positives",
            "true_negatives",
            "false_negatives",
        ):
            assert key in metrics


def test_scenario_runner_reproducible():
    a = run_scenario_evaluation(seeds=(7,), scenarios=("classic",))
    b = run_scenario_evaluation(seeds=(7,), scenarios=("classic",))
    assert [m["metrics"] for m in a] == [m["metrics"] for m in b]


def test_write_report_round_trips(tmp_path):
    out = tmp_path / "report.json"
    results = run_scenario_evaluation(seeds=(42,), scenarios=("noise",))
    write_report(results, str(out))
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert len(loaded) == 1
    assert loaded[0]["scenario"] == "noise"
