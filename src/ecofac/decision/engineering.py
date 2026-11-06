# Copyright 2026 Lightibulb
"""Structured engineering summary extraction from a stored decision record.

Reads only persisted evidence (``result.evidence.calculation_inputs`` and
``result.evidence.constraints_snapshot``). Performs **no** decision-engine
work — the extraction is presentational, and the resulting headroom values
are identical to those the engine computed when the decision was made.

The frontend MUST consume these values instead of deriving headroom itself.
"""

REQUIRED_CALC_INPUTS = (
    "current_it_load_kw",
    "current_facility_power_kw",
    "current_cooling_load_kw",
    "predicted_it_load_kw",
    "predicted_facility_power_kw",
    "predicted_cooling_load_kw",
    "pue",
    "pue_provenance",
)


def _headroom(observed: float, capacity: float, reserve_pct: float) -> dict:
    """Return the headroom picture for one observed value against one limit."""
    permitted = capacity * (1.0 - reserve_pct)
    return {
        "physical_capacity_kw": round(capacity, 4),
        "permitted_kw": round(permitted, 4),
        "observed_kw": round(observed, 4),
        "headroom_to_permitted_kw": round(permitted - observed, 4),
        "headroom_to_physical_kw": round(capacity - observed, 4),
        "reserve_pct": reserve_pct,
    }


def build_engineering_summary(record: dict) -> dict:
    """Extract engineering values from a stored decision record.

    Raises ``ValueError`` when the record predates the PUE integration and
    therefore lacks the fields required for a complete summary.
    """
    result = record.get("result") or {}
    evidence = result.get("evidence") or {}
    calc_inputs = evidence.get("calculation_inputs") or {}
    constraints = evidence.get("constraints_snapshot") or {}

    missing = [k for k in REQUIRED_CALC_INPUTS if k not in calc_inputs]
    if missing:
        msg = (
            "Decision record lacks engineering evidence "
            f"({', '.join(missing)}); it predates the PUE integration"
        )
        raise ValueError(msg)

    elec = constraints.get("electrical") or {}
    cool = constraints.get("cooling") or {}
    elec_cap = float(elec.get("capacity", 0.0))
    elec_res = float(elec.get("reserve_pct", 0.0))
    cool_cap = float(cool.get("capacity", 0.0))
    cool_res = float(cool.get("reserve_pct", 0.0))

    return {
        "pue": float(calc_inputs["pue"]),
        "pue_provenance": str(calc_inputs["pue_provenance"]),
        "current_it_load_kw": float(calc_inputs["current_it_load_kw"]),
        "current_facility_power_kw": float(calc_inputs["current_facility_power_kw"]),
        "current_cooling_load_kw": float(calc_inputs["current_cooling_load_kw"]),
        "predicted_it_load_kw": float(calc_inputs["predicted_it_load_kw"]),
        "predicted_facility_power_kw": float(calc_inputs["predicted_facility_power_kw"]),
        "predicted_cooling_load_kw": float(calc_inputs["predicted_cooling_load_kw"]),
        "current_electrical": _headroom(
            float(calc_inputs["current_facility_power_kw"]),
            elec_cap,
            elec_res,
        ),
        "predicted_electrical": _headroom(
            float(calc_inputs["predicted_facility_power_kw"]),
            elec_cap,
            elec_res,
        ),
        "current_cooling": _headroom(
            float(calc_inputs["current_cooling_load_kw"]),
            cool_cap,
            cool_res,
        ),
        "predicted_cooling": _headroom(
            float(calc_inputs["predicted_cooling_load_kw"]),
            cool_cap,
            cool_res,
        ),
    }
