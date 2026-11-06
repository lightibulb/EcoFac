# Copyright 2026 Lightibulb
"""Scenario Intelligence analysis — bottlenecks, comparison, sensitivity.

All analysis derives from the persisted result of a canonical decision
evaluation. There is no parallel decision path and no client-side
engineering math.

Bottleneck classification uses documented policy constants
(APPROACHING_PHYSICAL_RATIO, APPROACHING_PERMITTED_RATIO,
INSUFFICIENT_MARGIN_RATIO). They are policy choices, not calibrated
confidence scores.
"""

import copy
from typing import Any

import pydantic

from ecofac.decision.engineering import build_engineering_summary
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)

APPROACHING_PHYSICAL_RATIO = 0.10
APPROACHING_PERMITTED_RATIO = 0.10
INSUFFICIENT_MARGIN_RATIO = 0.20

MULTIPLICATIVE_STEPS = (0.8, 0.9, 1.0, 1.1, 1.2)
ADDITIVE_STEPS = (-0.05, -0.02, 0.0, 0.02, 0.05)


class BottleneckFinding(pydantic.BaseModel):
    """One bottleneck detected in a scenario."""

    category: str
    state: str
    constraint_name: str
    severity: str
    headroom_to_permitted_kw: float | None
    headroom_to_physical_kw: float | None
    permitted_kw: float | None
    physical_capacity_kw: float | None
    reason: str


class ScenarioBottleneckReport(pydantic.BaseModel):
    """Bottleneck analysis for one scenario."""

    scenario_id: str
    status: str
    precedence: str
    has_enough_information: bool
    reason_if_insufficient: str | None
    most_restrictive_constraint: str | None
    findings: list[BottleneckFinding]


def _extract_constraints(
    record: dict,
) -> tuple[list[dict], list[dict]]:
    """Return (current_constraints, predicted_constraints) from a record."""
    result = record.get("result") or {}
    sm = result.get("state_machine_decision") or {}
    constraints = sm.get("evaluated_constraints") or []

    elecs = [c for c in constraints if c.get("name") == "electrical"]
    cools = [c for c in constraints if c.get("name") == "cooling"]

    current: list[dict] = []
    predicted: list[dict] = []
    for pair in (elecs, cools):
        if len(pair) >= 2:
            current.append(pair[0])
            predicted.append(pair[1])
        elif len(pair) == 1:
            predicted.append(pair[0])
    return current, predicted


def _classify_constraint(constraint: dict, state: str) -> BottleneckFinding | None:
    """Return the most severe classification for one constraint, or None."""
    name = constraint.get("name")
    if name not in ("electrical", "cooling"):
        return None

    h_perm = constraint.get("headroom_to_permitted")
    h_phys = constraint.get("headroom_to_physical")
    perm = constraint.get("permitted")
    phys = constraint.get("physical_capacity")

    if h_phys is not None and h_phys < 0:
        return BottleneckFinding(
            category=f"{name}_over_physical",
            state=state,
            constraint_name=name,
            severity="CRITICAL",
            headroom_to_permitted_kw=h_perm,
            headroom_to_physical_kw=h_phys,
            permitted_kw=perm,
            physical_capacity_kw=phys,
            reason=(f"{name} observed load exceeds physical capacity ({-h_phys:.2f} kW over)."),
        )

    if h_perm is not None and h_perm < 0:
        return BottleneckFinding(
            category=f"{name}_over_permitted",
            state=state,
            constraint_name=name,
            severity="CRITICAL",
            headroom_to_permitted_kw=h_perm,
            headroom_to_physical_kw=h_phys,
            permitted_kw=perm,
            physical_capacity_kw=phys,
            reason=(
                f"{name} observed load exceeds the permitted limit "
                f"({-h_perm:.2f} kW over after reserve)."
            ),
        )

    if h_phys is not None and phys and phys > 0:
        phys_ratio = h_phys / phys
        if phys_ratio < APPROACHING_PHYSICAL_RATIO:
            return BottleneckFinding(
                category=f"{name}_approaching_physical",
                state=state,
                constraint_name=name,
                severity="WARNING",
                headroom_to_permitted_kw=h_perm,
                headroom_to_physical_kw=h_phys,
                permitted_kw=perm,
                physical_capacity_kw=phys,
                reason=(
                    f"{name} is within "
                    f"{APPROACHING_PHYSICAL_RATIO * 100:.0f}% "
                    f"of physical capacity "
                    f"(headroom {h_phys:.2f} / {phys:.2f} kW)."
                ),
            )

    if h_perm is not None and perm and perm > 0:
        perm_ratio = h_perm / perm
        if perm_ratio < APPROACHING_PERMITTED_RATIO:
            return BottleneckFinding(
                category=f"{name}_approaching_permitted",
                state=state,
                constraint_name=name,
                severity="WARNING",
                headroom_to_permitted_kw=h_perm,
                headroom_to_physical_kw=h_phys,
                permitted_kw=perm,
                physical_capacity_kw=phys,
                reason=(
                    f"{name} is within "
                    f"{APPROACHING_PERMITTED_RATIO * 100:.0f}% of the "
                    f"permitted limit (headroom {h_perm:.2f} / "
                    f"{perm:.2f} kW)."
                ),
            )

    return None


def detect_bottlenecks(record: dict) -> ScenarioBottleneckReport:
    """Analyse a stored scenario for bottleneck conditions."""
    scenario_id = record["id"]
    status = record["status"]
    precedence = record["precedence"]

    current, predicted = _extract_constraints(record)

    if not current and not predicted:
        return ScenarioBottleneckReport(
            scenario_id=scenario_id,
            status=status,
            precedence=precedence,
            has_enough_information=False,
            reason_if_insufficient=(
                "No constraint evaluations were recorded. This typically "
                "means the proposal was invalid (negative predicted load) "
                "or data quality was ineligible."
            ),
            most_restrictive_constraint=None,
            findings=[],
        )

    findings: list[BottleneckFinding] = []

    for constraint in current:
        f = _classify_constraint(constraint, "current")
        if f is not None:
            findings.append(f)

    for constraint in predicted:
        f = _classify_constraint(constraint, "predicted")
        if f is not None:
            findings.append(f)

    most_restrictive: str | None = None
    numeric_preds = [p for p in predicted if p.get("headroom_to_permitted") is not None]
    if numeric_preds:
        worst = min(
            numeric_preds,
            key=lambda p: p["headroom_to_permitted"],
        )
        most_restrictive = worst["name"]
        h = worst["headroom_to_permitted"]
        perm_val = worst.get("permitted") or 0.0
        if h < 0:
            severity = "CRITICAL"
        elif perm_val > 0 and h / perm_val < APPROACHING_PERMITTED_RATIO:
            severity = "WARNING"
        else:
            severity = "INFO"
        findings.insert(
            0,
            BottleneckFinding(
                category="most_restrictive",
                state="predicted",
                constraint_name=worst["name"],
                severity=severity,
                headroom_to_permitted_kw=h,
                headroom_to_physical_kw=worst.get("headroom_to_physical"),
                permitted_kw=perm_val,
                physical_capacity_kw=worst.get("physical_capacity"),
                reason=(
                    f"{worst['name']} has the smallest predicted headroom "
                    f"({h:.2f} kW) among all evaluated constraints."
                ),
            ),
        )

    if status == "APPROVE":
        for p in predicted:
            h = p.get("headroom_to_permitted")
            perm = p.get("permitted")
            if h is None or perm is None or perm <= 0:
                continue
            ratio = h / perm
            if APPROACHING_PERMITTED_RATIO <= ratio < INSUFFICIENT_MARGIN_RATIO:
                findings.append(
                    BottleneckFinding(
                        category=f"{p['name']}_insufficient_margin",
                        state="predicted",
                        constraint_name=p["name"],
                        severity="INFO",
                        headroom_to_permitted_kw=h,
                        headroom_to_physical_kw=p.get("headroom_to_physical"),
                        permitted_kw=perm,
                        physical_capacity_kw=p.get("physical_capacity"),
                        reason=(
                            f"Decision is APPROVE but {p['name']} headroom is "
                            f"{ratio * 100:.1f}% of permitted — below the "
                            f"{INSUFFICIENT_MARGIN_RATIO * 100:.0f}% comfort "
                            "margin."
                        ),
                    )
                )

    return ScenarioBottleneckReport(
        scenario_id=scenario_id,
        status=status,
        precedence=precedence,
        has_enough_information=True,
        reason_if_insufficient=None,
        most_restrictive_constraint=most_restrictive,
        findings=findings,
    )


class ScenarioSnapshot(pydantic.BaseModel):
    """Compact view of one scenario for comparison."""

    id: str
    name: str
    status: str
    precedence: str
    config_version: str
    engine_version: str | None
    pue: float | None
    pue_provenance: str | None
    current_it_load_kw: float | None
    current_facility_power_kw: float | None
    current_cooling_load_kw: float | None
    predicted_it_load_kw: float | None
    predicted_facility_power_kw: float | None
    predicted_cooling_load_kw: float | None
    current_electrical_headroom_kw: float | None
    predicted_electrical_headroom_kw: float | None
    current_cooling_headroom_kw: float | None
    predicted_cooling_headroom_kw: float | None
    data_quality_level: str | None
    model_uncertainty_level: str | None
    most_restrictive_constraint: str | None


class InputDiffEntry(pydantic.BaseModel):
    """Comparison of one input field across scenarios."""

    field_name: str
    units: str | None
    values: list[Any]
    differs: bool


class ScenarioComparisonResponse(pydantic.BaseModel):
    """Comparison of multiple scenarios."""

    scenarios: list[ScenarioSnapshot]
    input_differences: list[InputDiffEntry]
    mixed_config_versions: bool
    config_versions: list[str]
    mixed_engine_versions: bool = False
    engine_versions: list[str] = pydantic.Field(default_factory=list)
    outcome_explanation: str


_INPUT_FIELDS = (
    ("it_load_kw", "kW", ("inputs", "it_load", "value")),
    ("cooling_load_kw", "kW", ("inputs", "cooling_load", "value")),
    ("pue", "ratio", ("inputs", "pue", "value")),
    ("proposed_it_delta_kw", "kW", ("inputs", "proposed_it_delta")),
    (
        "proposed_cooling_delta_kw",
        "kW",
        ("inputs", "proposed_cooling_delta"),
    ),
    ("electrical_capacity_kw", "kW", ("config", "electrical", "capacity")),
    ("cooling_capacity_kw", "kW", ("config", "cooling", "capacity")),
    (
        "electrical_reserve_pct",
        "ratio",
        ("config", "electrical", "reserve_pct"),
    ),
    (
        "cooling_reserve_pct",
        "ratio",
        ("config", "cooling", "reserve_pct"),
    ),
)


def _extract_value(record: dict, path: tuple[str, ...]) -> Any:
    """Safely walk a nested path; return None on missing segment."""
    current: Any = record
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _headroom(eng: dict, key: str) -> float | None:
    sub = eng.get(key)
    if not isinstance(sub, dict):
        return None
    return sub.get("headroom_to_permitted_kw")


def _build_snapshot(record: dict) -> ScenarioSnapshot:
    """Build a comparison snapshot for one scenario."""
    try:
        eng = build_engineering_summary(record)
    except ValueError:
        eng = None

    result = record.get("result") or {}
    uncertainty = result.get("uncertainty") or {}
    bottleneck = detect_bottlenecks(record)

    if eng is None:
        return ScenarioSnapshot(
            id=record["id"],
            name=record["name"],
            status=record["status"],
            precedence=record["precedence"],
            config_version=record["config_version"],
            engine_version=record.get("engine_version"),
            pue=None,
            pue_provenance=None,
            current_it_load_kw=None,
            current_facility_power_kw=None,
            current_cooling_load_kw=None,
            predicted_it_load_kw=None,
            predicted_facility_power_kw=None,
            predicted_cooling_load_kw=None,
            current_electrical_headroom_kw=None,
            predicted_electrical_headroom_kw=None,
            current_cooling_headroom_kw=None,
            predicted_cooling_headroom_kw=None,
            data_quality_level=uncertainty.get("data_quality_level"),
            model_uncertainty_level=uncertainty.get("model_uncertainty_level"),
            most_restrictive_constraint=(bottleneck.most_restrictive_constraint),
        )

    return ScenarioSnapshot(
        id=record["id"],
        name=record["name"],
        status=record["status"],
        precedence=record["precedence"],
        config_version=record["config_version"],
        engine_version=record.get("engine_version"),
        pue=eng.get("pue"),
        pue_provenance=eng.get("pue_provenance"),
        current_it_load_kw=eng.get("current_it_load_kw"),
        current_facility_power_kw=eng.get("current_facility_power_kw"),
        current_cooling_load_kw=eng.get("current_cooling_load_kw"),
        predicted_it_load_kw=eng.get("predicted_it_load_kw"),
        predicted_facility_power_kw=eng.get("predicted_facility_power_kw"),
        predicted_cooling_load_kw=eng.get("predicted_cooling_load_kw"),
        current_electrical_headroom_kw=_headroom(eng, "current_electrical"),
        predicted_electrical_headroom_kw=_headroom(eng, "predicted_electrical"),
        current_cooling_headroom_kw=_headroom(eng, "current_cooling"),
        predicted_cooling_headroom_kw=_headroom(eng, "predicted_cooling"),
        data_quality_level=uncertainty.get("data_quality_level"),
        model_uncertainty_level=uncertainty.get("model_uncertainty_level"),
        most_restrictive_constraint=(bottleneck.most_restrictive_constraint),
    )


def _build_explanation(
    snapshots: list[ScenarioSnapshot],
    diffs: list[InputDiffEntry],
) -> str:
    """Deterministic narrative explaining differences between scenarios."""
    differing = [d for d in diffs if d.differs]
    statuses = {s.status for s in snapshots}

    if not differing:
        if len(statuses) == 1:
            return (
                f"All {len(snapshots)} scenarios produce the same outcome "
                f"({next(iter(statuses))}) because no input values differ."
            )
        return (
            "All input values are identical, but outcomes differ "
            f"({', '.join(sorted(statuses))}). Check engine version and "
            "configuration metadata — identical inputs must produce "
            "identical outputs under the same engine/configuration version."
        )

    input_summary = "; ".join(
        f"{d.field_name}=[{', '.join(str(v) for v in d.values)}]" for d in differing
    )
    outcome_summary = "; ".join(f"{s.id[:8]}: {s.status}/{s.precedence}" for s in snapshots)
    return (
        f"Scenarios differ in: {input_summary}. Outcomes: "
        f"{outcome_summary}. See per-scenario constraint evaluations for "
        "the specific constraint responsible for each status difference."
    )


def compare_scenarios(records: list[dict]) -> ScenarioComparisonResponse:
    """Compare 2+ scenarios. All records must be loaded from storage."""
    if len(records) < 2:
        msg = "At least 2 scenarios are required for comparison"
        raise ValueError(msg)
    ids = [r.get("id") for r in records]
    if len(ids) != len(set(ids)):
        msg = "Duplicate scenario identifiers"
        raise ValueError(msg)

    snapshots = [_build_snapshot(r) for r in records]

    input_differences: list[InputDiffEntry] = []
    for name, units, path in _INPUT_FIELDS:
        values = [_extract_value(r, path) for r in records]
        normalized = []
        for v in values:
            if isinstance(v, float):
                normalized.append(round(v, 4))
            elif v is None:
                normalized.append("NULL")
            else:
                normalized.append(v)
        differs = len({str(v) for v in normalized}) > 1
        input_differences.append(
            InputDiffEntry(
                field_name=name,
                units=units,
                values=values,
                differs=differs,
            )
        )

    config_versions = sorted({r["config_version"] for r in records})
    mixed_config_versions = len(config_versions) > 1

    explanation = _build_explanation(snapshots, input_differences)

    return ScenarioComparisonResponse(
        scenarios=snapshots,
        input_differences=input_differences,
        mixed_config_versions=mixed_config_versions,
        config_versions=config_versions,
        outcome_explanation=explanation,
    )


class SensitivityStep(pydantic.BaseModel):
    """One swept value and its decision outcome."""

    value: float
    status: str
    precedence: str
    changed_from_baseline: bool


class ParameterSensitivity(pydantic.BaseModel):
    """Sweep results for one input parameter."""

    parameter: str
    units: str | None
    baseline_value: float
    steps: list[SensitivityStep]
    verdict_changed: bool


class SensitivityAnalysisResponse(pydantic.BaseModel):
    """Sensitivity analysis for one scenario."""

    scenario_id: str
    baseline_status: str
    baseline_precedence: str
    parameters: list[ParameterSensitivity]


_SWEEPS = (
    {
        "name": "it_load_kw",
        "units": "kW",
        "path": ("it_load", "value"),
        "section": "input",
        "min_value": 0.0,
        "mode": "multiplicative",
    },
    {
        "name": "cooling_load_kw",
        "units": "kW",
        "path": ("cooling_load", "value"),
        "section": "input",
        "min_value": 0.0,
        "mode": "multiplicative",
    },
    {
        "name": "pue",
        "units": "ratio",
        "path": ("pue", "value"),
        "section": "input",
        "min_value": 0.1,
        "mode": "multiplicative",
    },
    {
        "name": "electrical_capacity_kw",
        "units": "kW",
        "path": ("electrical", "capacity"),
        "section": "config",
        "min_value": 0.1,
        "mode": "multiplicative",
    },
    {
        "name": "cooling_capacity_kw",
        "units": "kW",
        "path": ("cooling", "capacity"),
        "section": "config",
        "min_value": 0.1,
        "mode": "multiplicative",
    },
    {
        "name": "electrical_reserve_pct",
        "units": "ratio",
        "path": ("electrical", "reserve_pct"),
        "section": "config",
        "min_value": 0.0,
        "max_value": 0.99,
        "mode": "additive",
    },
    {
        "name": "cooling_reserve_pct",
        "units": "ratio",
        "path": ("cooling", "reserve_pct"),
        "section": "config",
        "min_value": 0.0,
        "max_value": 0.99,
        "mode": "additive",
    },
)


def _get_path(container: dict, path: tuple[str, ...]) -> Any:
    current: Any = container
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_path(container: dict, path: tuple[str, ...], value: Any) -> None:
    current: Any = container
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return
        current = current[key]
    if isinstance(current, dict):
        current[path[-1]] = value


def _request_payload_from_record(record: dict) -> dict:
    """Reconstruct a TransparentDecisionRequest-shaped dict from a record."""
    payload = copy.deepcopy(record["inputs"])
    payload["config"] = copy.deepcopy(record["config"])
    payload["config_version"] = record["config_version"]
    if payload.get("pue") is None:
        payload["pue"] = {
            "name": "pue",
            "value": 1.0,
            "unit": "dimensionless",
            "source": "ASSUMED",
        }
    return payload


def analyze_sensitivity(record: dict) -> SensitivityAnalysisResponse:
    """Deterministic multiplicative/additive sweep over supported inputs."""
    base_payload = _request_payload_from_record(record)
    baseline = decide_transparently(TransparentDecisionRequest.model_validate(base_payload))
    baseline_status = baseline.status
    baseline_precedence = baseline.precedence

    parameters: list[ParameterSensitivity] = []
    for sweep in _SWEEPS:
        section = sweep["section"]
        container = base_payload if section == "input" else base_payload["config"]
        baseline_value = _get_path(container, sweep["path"])
        if not isinstance(baseline_value, int | float):
            continue

        if sweep["mode"] == "multiplicative":
            step_values = [float(baseline_value) * m for m in MULTIPLICATIVE_STEPS]
        else:
            step_values = [float(baseline_value) + a for a in ADDITIVE_STEPS]

        steps: list[SensitivityStep] = []
        for new_value in step_values:
            if new_value < sweep["min_value"]:
                continue
            max_val = sweep.get("max_value")
            if max_val is not None and new_value > max_val:
                continue

            mutated = copy.deepcopy(base_payload)
            target = mutated if section == "input" else mutated["config"]
            _set_path(target, sweep["path"], new_value)

            try:
                request = TransparentDecisionRequest.model_validate(mutated)
                result = decide_transparently(request)
            except (ValueError, pydantic.ValidationError):
                continue

            steps.append(
                SensitivityStep(
                    value=round(new_value, 4),
                    status=result.status,
                    precedence=result.precedence,
                    changed_from_baseline=result.status != baseline_status,
                )
            )

        if not steps:
            continue

        parameters.append(
            ParameterSensitivity(
                parameter=sweep["name"],
                units=sweep["units"],
                baseline_value=round(float(baseline_value), 4),
                steps=steps,
                verdict_changed=any(s.changed_from_baseline for s in steps),
            )
        )

    return SensitivityAnalysisResponse(
        scenario_id=record["id"],
        baseline_status=baseline_status,
        baseline_precedence=baseline_precedence,
        parameters=parameters,
    )
