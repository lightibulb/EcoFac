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

# All supported scenarios. "classic" is the byte-stable default used by tests
# and by the dashboard's "generate demo" button.
SCENARIOS = [
    "classic",
    "spike",
    "gradual",
    "persistent",
    "intermittent",
    "missing",
    "irregular",
    "noise",
    "flatline",
    "mixed",
]


def _gen_classic(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,
) -> tuple[list[dict], list[dict], dict]:
    """Baseline scenario. Byte-identical to the pre-refactor implementation."""
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
    stale_skipped = min(3, days) * 24 if buildings >= 3 and meters_per_bldg >= 2 else 0
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": stale_skipped,
    }


def _gen_gradual(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,
) -> tuple[list[dict], list[dict], dict]:
    """Each meter may drift upward by 2 kWh/day; explicit spikes on top."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings: list[dict] = []
    ground_truth: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            drift = 0.0
            for day in range(days):
                if rng.random() < 0.3:
                    drift += 2.0
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)
                    value = base_load + seasonal + schedule + noise + drift
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
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_persistent(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,
) -> tuple[list[dict], list[dict], dict]:
    """Day-N/2: base_load doubles (legitimate change, NOT labeled). Spikes on top."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    step_day = days // 2
    readings: list[dict] = []
    ground_truth: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                effective_load = base_load * (1.5 if day >= step_day else 1.0)
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)
                    value = effective_load + seasonal + schedule + noise
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
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_intermittent(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,  # noqa: ARG001 — kept for signature uniformity
) -> tuple[list[dict], list[dict], dict]:
    """Every third day, 3 consecutive afternoon hours are spiked."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings: list[dict] = []
    ground_truth: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                spike_day = (day % 3 == 0)
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 1.0)
                    value = base_load + seasonal + schedule + noise
                    is_spike = spike_day and 14 <= hour <= 16
                    if is_spike:
                        value *= rng.uniform(2.5, 3.5)
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
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_irregular(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,
) -> tuple[list[dict], list[dict], dict]:
    """Variable hour stepping (sometimes 30min, sometimes 2h)."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings: list[dict] = []
    ground_truth: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                hour = 0
                while hour < 24:
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
                    hour += 1 if rng.random() < 0.7 else 2
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_noise(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,  # noqa: ARG001 — signature consistency
) -> tuple[list[dict], list[dict], dict]:
    """High-noise series with zero injected anomalies (pure negative control)."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    readings: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                    schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                    noise = rng.gauss(0, 4.0)
                    value = base_load + seasonal + schedule + noise
                    readings.append({
                        "timestamp": ts_str,
                        "building_id": bldg_id,
                        "meter_id": meter_id,
                        "energy_value": round(value, 2),
                        "unit": "kWh",
                    })
    return readings, [], {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_flatline(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,  # noqa: ARG001 — kept for signature uniformity
) -> tuple[list[dict], list[dict], dict]:
    """Days 40-45: sensor stuck at 0.5. Day 46+: recovery. Label the recovery."""
    start_date = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    stuck_start = min(40, max(1, days // 2))
    stuck_end = min(stuck_start + 5, days - 1) if days > stuck_start + 1 else stuck_start
    readings: list[dict] = []
    ground_truth: list[dict] = []
    for b in range(1, buildings + 1):
        bldg_id = f"b{b}"
        for m in range(1, meters_per_bldg + 1):
            meter_id = f"m{b}-{m}"
            base_load = rng.uniform(10.0, 50.0)
            seasonal_amp = rng.uniform(5.0, 15.0)
            for day in range(days):
                current_date = start_date + dt.timedelta(days=day)
                is_weekend = current_date.weekday() >= 5
                for hour in range(24):
                    ts = current_date + dt.timedelta(hours=hour)
                    ts_str = ts.isoformat().replace("+00:00", "Z")
                    if stuck_start <= day <= stuck_end:
                        value = 0.5
                        is_anomaly = False
                    else:
                        seasonal = math.sin(2 * math.pi * day / 90) * seasonal_amp
                        schedule = 5.0 if not is_weekend and 8 <= hour <= 18 else 0.0
                        noise = rng.gauss(0, 1.0)
                        value = base_load + seasonal + schedule + noise
                        is_anomaly = (day == stuck_end + 1 and hour < 3)
                    readings.append({
                        "timestamp": ts_str,
                        "building_id": bldg_id,
                        "meter_id": meter_id,
                        "energy_value": round(value, 2),
                        "unit": "kWh",
                    })
                    if is_anomaly:
                        ground_truth.append({
                            "building_id": bldg_id,
                            "meter_id": meter_id,
                            "timestamp": ts_str,
                            "type": "recovery",
                        })
    return readings, ground_truth, {
        "missing_rate": 0.01,
        "dupes_rate": 0.01,
        "stale_skipped": 0,
    }


def _gen_mixed(
    rng: random.Random,
    days: int,
    buildings: int,
    meters_per_bldg: int,
    anomaly_prob: float,
) -> tuple[list[dict], list[dict], dict]:
    """Classic spikes + intermittent spikes + 10% missing."""
    readings, gt, cfg = _gen_classic(rng, days, buildings, meters_per_bldg, anomaly_prob)
    extra_gt: list[dict] = []
    for i, r in enumerate(readings):
        if i % 97 == 0:
            r["energy_value"] = round(r["energy_value"] * 2.5, 2)
            extra_gt.append({
                "building_id": r["building_id"],
                "meter_id": r["meter_id"],
                "timestamp": r["timestamp"],
                "type": "spike",
            })
    cfg["missing_rate"] = 0.10
    return readings, gt + extra_gt, cfg


def generate_data(
    *,
    filename: str = "synthetic_data.csv",
    days: int = 90,
    buildings: int = 5,
    meters_per_bldg: int = 2,
    seed: int | None = 42,
    anomaly_prob: float = 0.01,
    scenario: str = "classic",
) -> dict[str, Any]:
    """Generate synthetic data, reconciled ground truth, and an exact manifest.

    The default seed is fixed (42), so repeated default runs are identical.
    Pass ``seed=None`` for a fresh random seed. ``scenario`` selects the
    injected-defect profile; ``"classic"`` is byte-identical to the historical
    generator.
    """
    if scenario not in SCENARIOS:
        msg = f"Unknown scenario: {scenario}. Valid: {SCENARIOS}"
        raise ValueError(msg)
    if seed is None:
        seed = random.SystemRandom().randrange(2**32)
    rng = random.Random(seed)

    if scenario == "classic":
        readings, ground_truth, cfg = _gen_classic(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "spike":
        readings, ground_truth, cfg = _gen_classic(
            rng, days, buildings, meters_per_bldg, max(anomaly_prob, 0.03)
        )
    elif scenario == "missing":
        readings, ground_truth, cfg = _gen_classic(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
        cfg["missing_rate"] = 0.10
    elif scenario == "gradual":
        readings, ground_truth, cfg = _gen_gradual(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "persistent":
        readings, ground_truth, cfg = _gen_persistent(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "intermittent":
        readings, ground_truth, cfg = _gen_intermittent(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "irregular":
        readings, ground_truth, cfg = _gen_irregular(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "noise":
        readings, ground_truth, cfg = _gen_noise(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    elif scenario == "flatline":
        readings, ground_truth, cfg = _gen_flatline(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )
    else:  # mixed
        readings, ground_truth, cfg = _gen_mixed(
            rng, days, buildings, meters_per_bldg, anomaly_prob
        )

    # Common post-processing
    total_before = len(readings)
    readings = [r for r in readings if rng.random() > cfg["missing_rate"]]
    missing_count = total_before - len(readings)

    num_dupes = int(len(readings) * cfg["dupes_rate"])
    duplicates = rng.sample(readings, num_dupes) if num_dupes > 0 else []
    readings.extend(duplicates)
    rng.shuffle(readings)

    raw_injected = [dict(g) for g in ground_truth]

    present_set = {(r["meter_id"], r["timestamp"]) for r in readings}
    valid_ground_truth: list[dict] = []
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
        "duplicates": len(duplicates),
        "missing": missing_count,
        "stale_readings": cfg["stale_skipped"],
    }
    qt_path = path.parent / "quality_ground_truth.json"
    with qt_path.open("w", encoding="utf-8") as f:
        json.dump(quality_truth, f, indent=2)

    print(
        f"Generated {len(readings)} readings and "
        f"{len(ground_truth)} ground truth anomalies (scenario={scenario})."
    )

    manifest = {
        "scenario": scenario,
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
            "stale_readings": cfg["stale_skipped"],
        },
        "exact_injected_anomalies": ground_truth,
        "raw_injected_anomalies": raw_injected,
    }
    sm_path = path.parent / "scenario_manifest.json"
    with sm_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest
