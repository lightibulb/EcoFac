# Copyright 2026 Lightibulb
"""Calculates anomaly detection and data quality metrics."""
import itertools
import json
import math
import sqlite3


def calculate_pr_auc(precisions: list[float], recalls: list[float]) -> float:
    """Integrate a precision-recall curve with explicit (0,1) and (1,0) ends.

    Inputs are operating points, not arbitrary samples. Duplicate recall points
    are retained because they can represent vertical PR-curve segments.
    """
    if not precisions or not recalls or len(precisions) != len(recalls):
        return 0.0
    points = sorted(
        (max(0.0, min(1.0, float(r))), max(0.0, min(1.0, float(p))))
        for p, r in zip(precisions, recalls, strict=True)
    )
    points = [(0.0, 1.0), *points, (1.0, 0.0)]
    auc = 0.0
    for (r0, p0), (r1, p1) in itertools.pairwise(points):
        auc += max(0.0, r1 - r0) * (p0 + p1) / 2.0
    return max(0.0, min(1.0, auc))


def _ranked_pr_curve(
    predictions: list[sqlite3.Row], truth_set: set[tuple[str, str]]
) -> float:
    """Calculate threshold-swept PR-AUC, grouping equal anomaly scores."""
    if not truth_set or not predictions:
        return 0.0
    best: dict[tuple[str, str], float] = {}
    for prediction in predictions:
        try:
            params = json.loads(prediction["detection_parameters"] or "{}")
            score = abs(float(params.get("z_score", 0.0)))
            if not math.isfinite(score):
                score = 0.0
        except (TypeError, ValueError, json.JSONDecodeError):
            score = 0.0
        key = (prediction["meter_id"], prediction["timestamp_utc"])
        best[key] = max(best.get(key, 0.0), score)

    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    points_p: list[float] = []
    points_r: list[float] = []
    tp = 0
    fp = 0
    index = 0
    while index < len(ranked):
        score = ranked[index][1]
        # Apply all predictions at a tied score before recording the threshold.
        while index < len(ranked) and ranked[index][1] == score:
            if ranked[index][0] in truth_set:
                tp += 1
            else:
                fp += 1
            index += 1
        points_p.append(tp / (tp + fp))
        points_r.append(tp / len(truth_set))
    return calculate_pr_auc(points_p, points_r)

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
