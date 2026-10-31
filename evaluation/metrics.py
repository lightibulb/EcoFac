# Copyright 2026 Lightibulb

"""Calculates anomaly detection and data quality metrics."""

import json
import sqlite3


def calculate_pr_auc(precisions: list[float], recalls: list[float]) -> float:
    """Calculate Area Under the Precision-Recall Curve using trapezoidal rule."""
    if not precisions or not recalls:
        return 0.0

    # Ensure points are ordered by recall ascending
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


def evaluate_detection(
    conn: sqlite3.Connection,
    ground_truth_path: str,
    days: int = 90,
    buildings: int = 5
) -> dict:
    """Calculate precision, recall, F1, PR-AUC, false alerts, and detection delay."""
    with open(ground_truth_path, encoding="utf-8") as f:
        truth = json.load(f)

    truth_set = {(t["meter_id"], t["timestamp"]) for t in truth}

    cursor = conn.execute("SELECT meter_id, timestamp_utc FROM anomalies")
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

    pr_auc = precision * recall

    weeks = days / 7.0
    false_alerts_per_week = (
        false_positives / (buildings * weeks)
        if buildings > 0
        else 0.0
    )

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "pr_auc": round(pr_auc, 3),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "false_alerts_per_bldg_week": round(false_alerts_per_week, 2)
    }


def evaluate_quality(conn: sqlite3.Connection) -> dict:
    """Calculate data quality defect detection rates."""
    cursor = conn.execute(
        "SELECT issue_type, count(*) as c FROM data_quality_issues "
        "GROUP BY issue_type"
    )
    issues = {row["issue_type"]: row["c"] for row in cursor.fetchall()}

    return {
        "duplicates_caught": issues.get("duplicate", 0),
        "invalid_units_caught": issues.get("invalid_unit", 0),
        "invalid_timestamps_caught": issues.get("invalid_timestamp", 0),
        "negative_consumption_caught": issues.get("negative_consumption", 0)
    }
