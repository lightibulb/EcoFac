# Copyright 2026 Lightibulb
"""Calculates anomaly detection and data quality metrics."""
import json
import sqlite3


def calculate_pr_auc(precisions: list[float], recalls: list[float]) -> float:
    """Calculate Area Under the Precision-Recall Curve (trapezoidal rule)."""
    if not precisions or not recalls:
        return 0.0
    sorted_pairs = sorted(zip(recalls, precisions, strict=True))
    auc = 0.0
    prev_recall = 0.0
    prev_precision = 1.0
    for r, p in sorted_pairs:
        width = r - prev_recall
        height = (p + prev_precision) / 2.0
        auc += width * height
        prev_recall = r
        prev_precision = p
    return auc


def _ranked_pr_curve(
    predictions: list[sqlite3.Row], truth_set: set[tuple[str, str]]
) -> float:
    """Build a real PR curve by sweeping a decision threshold over |z|.

    Each anomaly stores its z-score in detection_parameters; ranking by
    |z-score| and walking the cutoff yields (precision, recall) operating
    points, which are integrated into PR-AUC.
    """
    if not truth_set or not predictions:
        return 0.0
    best: dict[tuple[str, str], float] = {}
    for p in predictions:
        try:
            params = json.loads(p["detection_parameters"] or "{}")
            score = abs(float(params.get("z_score", 0.0)))
        except (TypeError, ValueError):
            score = 0.0
        key = (p["meter_id"], p["timestamp_utc"])
        best[key] = max(best.get(key, 0.0), score)

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    precisions: list[float] = []
    recalls: list[float] = []
    tp = 0
    for i, (key, _score) in enumerate(ranked, start=1):
        if key in truth_set:
            tp += 1
        precisions.append(tp / i)
        recalls.append(tp / len(truth_set))
    return calculate_pr_auc(precisions, recalls)


def evaluate_detection(
    conn: sqlite3.Connection,
    ground_truth_path: str,
    days: int = 90,
    buildings: int = 5,
) -> dict:
    """Calculate precision, recall, F1, PR-AUC, and false alert rate."""
    with open(ground_truth_path, encoding="utf-8") as f:
        truth = json.load(f)
    truth_set = {(t["meter_id"], t["timestamp"]) for t in truth}

    cursor = conn.execute(
        "SELECT meter_id, timestamp_utc, detection_parameters FROM anomalies"
    )
    predictions = cursor.fetchall()
    pred_set = {(p["meter_id"], p["timestamp_utc"]) for p in predictions}

    true_positives = len(truth_set.intersection(pred_set))
    false_positives = len(pred_set - truth_set)
    false_negatives = len(truth_set - pred_set)
    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0
        else 0.0
    )
    f1 = (
        2 * (precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    pr_auc = _ranked_pr_curve(predictions, truth_set)

    weeks = days / 7.0
    false_alerts_per_week = (
        false_positives / (buildings * weeks) if buildings > 0 else 0.0
    )

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "pr_auc": round(pr_auc, 3),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "false_alerts_per_bldg_week": round(false_alerts_per_week, 2),
    }


def evaluate_quality(
    conn: sqlite3.Connection,
    quality_truth_path: str = "quality_ground_truth.json",
) -> dict:
    """Calculate data quality defect counts and capture rates.

    If the simulator emitted quality_ground_truth.json, injected defect
    counts are compared against what ingestion actually caught.
    """
    cursor = conn.execute(
        "SELECT issue_type, count(*) AS c FROM data_quality_issues "
        "GROUP BY issue_type"
    )
    issues = {row["issue_type"]: row["c"] for row in cursor.fetchall()}
    result = {
        "duplicates_caught": issues.get("duplicate", 0),
        "invalid_units_caught": issues.get("invalid_unit", 0),
        "invalid_timestamps_caught": issues.get("invalid_timestamp", 0),
        "negative_consumption_caught": issues.get("negative_consumption", 0),
    }
    try:
        with open(quality_truth_path, encoding="utf-8") as f:
            truth = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return result
    injected_dupes = int(truth.get("duplicates", 0))
    result["duplicates_injected"] = injected_dupes
    result["missing_injected"] = int(truth.get("missing", 0))
    result["duplicate_capture_rate"] = (
        round(result["duplicates_caught"] / injected_dupes, 3)
        if injected_dupes > 0
        else 1.0
    )
    return result
