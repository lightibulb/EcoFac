# Copyright 2026 Lightibulb

"""Reproducible synthetic data generator for EcoFac evaluation."""

import csv
import datetime as dt
import json
import math
import pathlib
import random


def generate_data(
    *,
    filename: str = "synthetic_data.csv",
    days: int = 90,
    buildings: int = 5,
    meters_per_bldg: int = 2,
    seed: int = 42,
    anomaly_prob: float = 0.01,
) -> None:
    """Generate synthetic CSV data and ground truth labels."""
    random.seed(seed)

    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings = []
    ground_truth = []

    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"

            base_load = random.uniform(10.0, 50.0)
            seasonal_amp = random.uniform(5.0, 15.0)

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
                    noise = random.gauss(0, 1.0)

                    value = base_load + seasonal + schedule + noise

                    if random.random() < anomaly_prob and value > 0:
                        value *= random.uniform(1.5, 3.0)
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

    readings = [r for r in readings if random.random() > 0.01]
    duplicates = random.sample(readings, int(len(readings) * 0.01))
    readings.extend(duplicates)
    random.shuffle(readings)

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

    print(
        f"Generated {len(readings)} readings and "
        f"{len(ground_truth)} ground truth anomalies."
    )
