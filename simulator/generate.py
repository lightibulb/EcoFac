# Copyright 2026 Lightibulb

"""Reproducible synthetic data generator for EcoFac evaluation."""

import csv
import datetime as dt
import functools
import json
import math
import pathlib
import random


def _preserve_random_state(func):  # noqa: ANN001, ANN202
    """Wrap a function so it never mutates global random state."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        state = random.getstate()
        try:
            return func(*args, **kwargs)
        finally:
            random.setstate(state)
    return wrapper


@_preserve_random_state
def generate_data(
    *,
    filename: str = "synthetic_data.csv",
    days: int = 90,
    buildings: int = 5,
    meters_per_bldg: int = 2,
    seed: int | None = None,
    anomaly_prob: float = 0.01,
) -> dict:
    """Generate synthetic data, reconciled truth, and an exact run manifest.

    A missing seed produces a fresh scenario. Pass an integer seed to reproduce
    the same scenario without mutating Python's process-global random generator.
    """
    if days < 1 or buildings < 1 or meters_per_bldg < 1:
        raise ValueError("days, buildings, and meters_per_bldg must be positive")
    if not 0.0 <= anomaly_prob <= 1.0:
        raise ValueError("anomaly_prob must be between 0 and 1")

    # SystemRandom avoids relying on a fixed default seed. The local RNG keeps
    # simulator calls isolated from unrelated application randomness.
    actual_seed = int(seed) if seed is not None else random.SystemRandom().randint(0, 2**32 - 1)
    rng = random.Random(actual_seed)
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings = []
    ground_truth = []

    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                # Deliberate stale-data scenario: last 3 days of b3/m2.
                if b == 3 and m == 2 and day >= days - 3:
                    continue
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)
                    baseline_value = base_load + seasonal + schedule + noise
                    value = baseline_value
                    if rng.random() < anomaly_prob and value > 0:
                        multiplier = rng.uniform(1.5, 3.0)
                        value *= multiplier
                        ground_truth.append({
                            "building_id": bldg_id,
                            "meter_id": meter_id,
                            "timestamp": ts_str,
                            "type": "spike",
                            "baseline_value": round(baseline_value, 6),
                            "observed_value": round(value, 2),
                            "injected_multiplier": round(multiplier, 6),
                        })
                    readings.append({
                        "timestamp": ts_str,
                        "building_id": bldg_id,
                        "meter_id": meter_id,
                        "energy_value": round(value, 2),
                        "unit": "kWh",
                    })

    # Missingness is applied before truth is finalized. A label survives only
    # when its exact reading survives, preventing impossible false negatives.
    nominal_reading_count = days * buildings * meters_per_bldg * 24
    readings = [r for r in readings if rng.random() > 0.01]
    surviving_keys = {(r["meter_id"], r["timestamp"]) for r in readings}
    ground_truth = [
        label for label in ground_truth
        if (label["meter_id"], label["timestamp"]) in surviving_keys
    ]
    # Includes both random packet loss and the deliberately stale meter gap.
    missing_count = nominal_reading_count - len(readings)

    duplicate_count = int(len(readings) * 0.01)
    duplicates = rng.sample(readings, duplicate_count) if duplicate_count else []
    readings.extend(duplicates)
    rng.shuffle(readings)

    path = pathlib.Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "building_id", "meter_id", "energy_value", "unit"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(readings)

    gt_path = path.parent / "ground_truth.json"
    with gt_path.open("w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    quality_truth = {
        "missing": missing_count,
        "duplicates": duplicate_count,
        "invalid_units": 0,
        "invalid_timestamps": 0,
        "negative_consumption": 0,
        "notes": "Missing and duplicate counts are injected simulator defects.",
    }
    quality_path = path.parent / "quality_ground_truth.json"
    with quality_path.open("w", encoding="utf-8") as f:
        json.dump(quality_truth, f, indent=2)

    manifest = {
        "schema_version": 1,
        "seed": actual_seed,
        "seed_was_explicit": seed is not None,
        "config": {
            "days": days,
            "buildings": buildings,
            "meters_per_building": meters_per_bldg,
            "anomaly_probability": anomaly_prob,
            "missing_probability": 0.01,
            "duplicate_fraction_of_survivors": 0.01,
            "stale_meter": "m3-2",
            "stale_days_at_end": 3,
        },
        "counts": {
            "readings_after_missing_before_duplicates": len(readings) - duplicate_count,
            "csv_rows_including_duplicates": len(readings),
            "missing_injected": missing_count,
            "duplicates_injected": duplicate_count,
            "ground_truth_anomalies_after_reconciliation": len(ground_truth),
        },
        "exact_injected_anomalies": ground_truth,
        "quality_ground_truth": quality_truth,
        "artifacts": {
            "readings_csv": path.name,
            "detection_ground_truth": gt_path.name,
            "quality_ground_truth": quality_path.name,
        },
    }
    manifest_path = path.parent / "scenario_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"Generated {len(readings)} CSV rows (including {duplicate_count} duplicates), "
        f"{missing_count} missing readings, and {len(ground_truth)} reconciled anomalies. "
        f"Scenario seed: {actual_seed}"
    )
    return manifest

