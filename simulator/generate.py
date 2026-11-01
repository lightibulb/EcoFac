# Copyright 2026 Lightibulb

"""Reproducible synthetic data generator for EcoFac evaluation."""

import csv
import datetime as dt
import json
import math
import pathlib
import random

DETECTION_WINDOW = 24


def generate_data(
    *,
    filename: str = "synthetic_data.csv",
    days: int = 90,
    buildings: int = 5,
    meters_per_bldg: int = 2,
    seed: int = 42,
    anomaly_prob: float = 0.01,
) -> None:
    """Generate synthetic CSV data plus ground-truth labels.

    Writes three files next to ``filename``: the CSV, ``ground_truth.json``
    (injected spikes that are detectable in principle) and
    ``quality_ground_truth.json`` (injected defect counts).
    """
    rng = random.Random(seed)  # local RNG: same stream, no shared global state

    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings = []
    ground_truth = []
    stale_skipped = 0

    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"

            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)

            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5

                # Stale data simulation (last 3 days of meter 2, building 3)
                if b == 3 and m == 2 and day >= days - 3:
                    stale_skipped += 24
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

    generated = len(readings)
    readings = [r for r in readings if rng.random() > 0.01]
    missing = generated - len(readings)

    # Reconcile labels with what actually survived the missing-data drop. A
    # spike is only a fair target if its reading exists and the detectors have
    # a full history window before it.
    position: dict[tuple[str, str], int] = {}
    per_meter: dict[str, int] = {}
    for r in readings:
        index = per_meter.get(r["meter_id"], 0)
        position[(r["meter_id"], r["timestamp"])] = index
        per_meter[r["meter_id"]] = index + 1
    ground_truth = [
        t for t in ground_truth
        if position.get((t["meter_id"], t["timestamp"]), -1) >= DETECTION_WINDOW
    ]

    duplicates = rng.sample(readings, int(len(readings) * 0.01))
    readings.extend(duplicates)
    rng.shuffle(readings)

    path = pathlib.Path(filename)
    fields = ["timestamp", "building_id", "meter_id", "energy_value", "unit"]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(readings)

    # Ensure ground_truth.json is always written in the same directory
    gt_path = path.parent / "ground_truth.json"
    with gt_path.open("w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    quality_truth = {
        "duplicates": len(duplicates),
        "missing": missing,
        "stale_readings": stale_skipped,
    }
    with (path.parent / "quality_ground_truth.json").open("w", encoding="utf-8") as f:
        json.dump(quality_truth, f, indent=2)

    print(
        f"Generated {len(readings)} readings and "
        f"{len(ground_truth)} ground truth anomalies."
    )
