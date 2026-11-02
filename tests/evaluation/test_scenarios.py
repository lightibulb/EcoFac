# Copyright 2026 Lightibulb

"""Scenario generation, reconciliation, and reproducibility tests."""

import csv
import json

import pytest

from simulator.generate import SCENARIOS, generate_data


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_ground_truth_reconciles(scenario, tmp_path):
    """Every GT label must have a corresponding row in the CSV."""
    scenario_dir = tmp_path / scenario
    scenario_dir.mkdir()
    generate_data(
        filename=str(scenario_dir / "data.csv"),
        scenario=scenario,
        days=5,
        buildings=2,
        seed=42,
    )

    rows = list(
        csv.DictReader((scenario_dir / "data.csv").open(encoding="utf-8"))
    )
    present = {(r["meter_id"], r["timestamp"]) for r in rows}
    gt = json.loads((scenario_dir / "ground_truth.json").read_text(encoding="utf-8"))

    for label in gt:
        assert (label["meter_id"], label["timestamp"]) in present, (
            f"{scenario}: GT label {label} has no matching reading"
        )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_quality_schema(scenario, tmp_path):
    """Quality ground truth must expose the exact three documented keys."""
    scenario_dir = tmp_path / scenario
    scenario_dir.mkdir()
    generate_data(
        filename=str(scenario_dir / "data.csv"),
        scenario=scenario,
        days=5,
        buildings=2,
        seed=42,
    )
    quality = json.loads(
        (scenario_dir / "quality_ground_truth.json").read_text(encoding="utf-8")
    )
    assert set(quality) == {"duplicates", "missing", "stale_readings"}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_reproducible(scenario, tmp_path):
    """Same seed + scenario → byte-identical CSV and identical manifest GT."""
    a_dir = tmp_path / f"{scenario}_a"
    b_dir = tmp_path / f"{scenario}_b"
    a_dir.mkdir()
    b_dir.mkdir()

    first = generate_data(
        filename=str(a_dir / "data.csv"), scenario=scenario, days=3, buildings=1, seed=7
    )
    second = generate_data(
        filename=str(b_dir / "data.csv"), scenario=scenario, days=3, buildings=1, seed=7
    )
    assert (a_dir / "data.csv").read_text() == (b_dir / "data.csv").read_text()
    assert (
        first["exact_injected_anomalies"] == second["exact_injected_anomalies"]
    )


def test_unknown_scenario_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown scenario"):
        generate_data(
            filename=str(tmp_path / "data.csv"),
            scenario="nope",
            days=1,
            buildings=1,
        )


def test_classic_default_preserved(tmp_path):
    """Calling generate_data() without scenario must equal scenario='classic'."""
    a_dir = tmp_path / "default"
    b_dir = tmp_path / "explicit"
    a_dir.mkdir()
    b_dir.mkdir()
    generate_data(filename=str(a_dir / "data.csv"), days=2, seed=42)
    generate_data(
        filename=str(b_dir / "data.csv"), scenario="classic", days=2, seed=42
    )
    assert (a_dir / "data.csv").read_text() == (b_dir / "data.csv").read_text()
