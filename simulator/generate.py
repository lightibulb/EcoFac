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
    seed: int = 42,
    anomaly_prob: float = 0.01,
) -> dict:
    """Generate synthetic CSV data, ground-truth labels, and manifest."""
    rng = random.Random(seed)

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

                if b == 3 and m == 2 and day >= days - 3:
                    continue

                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")

                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)

                    value = base_load + seasonal + schedule + noise

                    if rng.random() < anomaly_prob and value > 0:
                        value *= rng.uniform(1.5, 3.0)
                        ground_truth.append({
                            "building_id": bldg_id,
                            "meter_id": meter_id,
                            "timestamp": ts_str,
                            "type": "spike",
                        })

                    readings.append({
                        "timestamp": ts_str,
                        "building_id": bldg_id,
                        "meter_id": meter_id,
                        "energy_value": round(value, 2),
                        "unit": "kWh",
                    })

    # Inject missing readings (drop ~1%)
    missing_injected = 0
    kept_readings = []
    for r in readings:
        if rng.random() > 0.01:
            kept_readings.append(r)
        else:
            missing_injected += 1
    readings = kept_readings

    # Reconcile: only keep anomalies whose reading is still in the CSV
    present = {(r["meter_id"], r["timestamp"]) for r in readings}
    ground_truth = [a for a in ground_truth if (a["meter_id"], a["timestamp"]) in present]
    exact_injected_anomalies = len(ground_truth)

    # Inject duplicates (~1%)
    duplicates_injected = int(len(readings) * 0.01)
    if duplicates_injected > 0:
        duplicates = rng.sample(readings, duplicates_injected)
        readings.extend(duplicates)

    rng.shuffle(readings)

    path = pathlib.Path(filename)
    fields = ["timestamp", "building_id", "meter_id", "energy_value", "unit"]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(readings)

    gt_path = path.parent / "ground_truth.json"
    with gt_path.open("w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    quality_truth = {
        "missing": missing_injected,
        "duplicates": duplicates_injected,
        "total_readings": len(readings),
    }
    quality_path = path.parent / "quality_ground_truth.json"
    with quality_path.open("w", encoding="utf-8") as f:
        json.dump(quality_truth, f, indent=2)

    manifest = {
        "seed": seed,
        "exact_injected_anomalies": exact_injected_anomalies,
        "counts": {
            "missing_injected": missing_injected,
            "duplicates_injected": duplicates_injected,
            "total_rows_written": len(readings),
        },
    }
    manifest_path = path.parent / "scenario_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"Generated {len(readings)} readings and "
        f"{len(ground_truth)} ground truth anomalies."
    )
    return manifest
