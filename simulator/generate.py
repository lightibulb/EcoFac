# Copyright 2026 Lightibulb
"""Reproducible synthetic data generator for EcoFac evaluation."""
import csv
import datetime as dt
import json
import math
import pathlib
import random
from typing import Any

DETECTION_WINDOW = 24

def generate_data(
    *,
    filename: str = "synthetic_data.csv",
    days: int = 90,
    buildings: int = 5,
    meters_per_bldg: int = 2,
    seed: int | None = None,
    anomaly_prob: float = 0.01,
) -> dict[str, Any]:
    """Generate synthetic CSV data and ground truth labels.

    With ``seed=None`` a fresh random seed is chosen; it is always returned in
    the manifest so any run can be reproduced by passing it back in.
    """
    if seed is None:
        seed = random.SystemRandom().randrange(2**32)
    # Isolate RNG to prevent modifying the global random state
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
                # Stale data simulation (last 3 days of meter 2, building 3)
                if b == 3 and m == 2 and day >= days - 3:
                    continue
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)
                    value = base_load + seasonal + schedule + noise

                    is_spike = False
                    if rng.random() < anomaly_prob and value > 0:
                        value *= rng.uniform(1.5, 3.0)
                        is_spike = True

                    readings.append({
                        "timestamp": ts_str,
                        "building_id": bldg_id,
                        "meter_id": meter_id,
                        "energy_value": round(value, 2),
                        "unit": "kWh",
                    })
                    if is_spike:
                        ground_truth.append({
                            "building_id": bldg_id,
                            "meter_id": meter_id,
                            "timestamp": ts_str,
                            "type": "spike",
                        })

    # Inject missing data (drop 1% of rows randomly)
    total_before = len(readings)
    readings = [r for r in readings if rng.random() > 0.01]
    missing_count = total_before - len(readings)

    # Inject duplicates (duplicate 1% of rows)
    num_dupes = int(len(readings) * 0.01)
    duplicates = rng.sample(readings, num_dupes) if num_dupes > 0 else []
    readings.extend(duplicates)
    rng.shuffle(readings)

    # Filter ground truth to ensure fairness:
    # 1. The spike itself must survive the missing data drop.
    # 2. There must be at least DETECTION_WINDOW earlier readings for this meter in the final CSV.
    present_set = {(r["meter_id"], r["timestamp"]) for r in readings}
    valid_ground_truth = []
    for g in ground_truth:
        m_id = g["meter_id"]
        t_id = g["timestamp"]
        if (m_id, t_id) not in present_set:
            continue
        earlier = sum(1 for (m, t) in present_set if m == m_id and t < t_id)
        if earlier >= DETECTION_WINDOW:
            valid_ground_truth.append(g)

    ground_truth = valid_ground_truth

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

    # Write quality ground truth so ingestion capture rates are measurable
    stale_readings_count = 0
    if buildings >= 3 and meters_per_bldg >= 2:
        stale_readings_count = 3 * 24

    quality_truth = {
        "duplicates": len(duplicates),
        "missing": missing_count,
        "stale_readings": stale_readings_count,
    }
    qt_path = path.parent / "quality_ground_truth.json"
    with qt_path.open("w", encoding="utf-8") as f:
        json.dump(quality_truth, f, indent=2)

    print(
        f"Generated {len(readings)} readings and "
        f"{len(ground_truth)} ground truth anomalies."
    )

    return {
        "seed": seed,
        "files": {
            "data": str(path),
            "ground_truth": str(gt_path),
            "quality_ground_truth": str(qt_path),
        },
        "counts": {
            "readings": len(readings),
            "anomalies": len(ground_truth),
            "missing_injected": missing_count,
            "duplicates_injected": len(duplicates),
            "stale_readings": stale_readings_count,
        },
    }
