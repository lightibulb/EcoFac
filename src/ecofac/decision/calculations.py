# Copyright 2026 Lightibulb
"""Engineering calculation layer for data-centre operational decisions.

Pure mathematical functions. No I/O, no database, no API. Produces
reproducible current-state and predicted-state operational measurements
suitable for the decision engine, evidence records, and evaluation.

All formulas and assumptions are recorded in the returned CalculationReport
so downstream consumers can reproduce and audit every number.

Modeling note (see MODELING_NOTES):

The relation between IT electrical load and cooling load is a simplification,
not a thermal model. See ``heat_conversion_factor`` on ``CoolingCapacity`` for
the extensibility hook.
"""

from enum import StrEnum

import pydantic

from ecofac.decision.numeric import MAX_PUE, MIN_PUE

DEFAULT_HEAT_CONVERSION_FACTOR = 1.0

# Single source of truth for the formulas the engine evaluates. Evidence
# generation (``decision.transparent``) reads these strings, and the functions
# below implement them, so the recorded formula cannot drift from the code.
FORMULAS: dict[str, str] = {
    "facility_power_kw": "it_load_kw * pue_value",
    "predicted_it_load_kw": "current_it_load_kw + proposed_it_delta",
    "predicted_facility_power_kw": "predicted_it_load_kw * pue_value",
    "predicted_cooling_kw": "current_cooling_kw + proposed_cooling_delta",
    "permitted_kw": "capacity * (1 - reserve_pct)",
    "headroom_to_permitted": "permitted_kw - observed",
    "headroom_to_physical": "capacity - observed",
}

# Cooling in the decision path is a DIRECTLY MEASURED (or caller-stated) load
# plus an explicit proposed delta. It is NOT derived from IT load. The modelled
# alternative (``calculate_cooling``: heat load = IT load * heat_conversion_factor)
# is a demand estimate used by ``build_report`` only; never mix the two.
COOLING_BASIS_MEASURED = "measured_cooling_load"
COOLING_BASIS_MODELLED = "modelled_cooling_demand"

MODELING_NOTES = {
    "it_heat_approximation": (
        "IT electrical load in kW is treated as approximately equal to IT heat "
        "output in kW (factor = 1.0 by default). This is a simplification, "
        "not a complete thermal model."
    ),
    "pue_source": (
        "PUE is supplied by the caller as a measured, configured, or assumed "
        "value. The engine does not treat PUE as universally constant."
    ),
    "future_extensibility": (
        "heat_conversion_factor is an explicit parameter so future airflow, "
        "temperature, and cooling-efficiency models can override the default "
        "approximation without changing this module's interface."
    ),
}


class PUESource(StrEnum):
    """Where a PUE value came from."""

    MEASURED = "MEASURED"
    CONFIGURED = "CONFIGURED"
    ASSUMED = "ASSUMED"


class PUEResult(pydantic.BaseModel):
    """A PUE value plus its provenance."""

    value: float = pydantic.Field(ge=MIN_PUE, le=MAX_PUE, allow_inf_nan=False)
    source: PUESource
    notes: str | None = None


class LoadChange(pydantic.BaseModel):
    """A single proposed change to IT load. Negative values are reductions."""

    it_load_delta_kw: float
    description: str | None = None


class ElectricalCapacity(pydantic.BaseModel):
    """Electrical limits supplied by the operator."""

    capacity_kw: float = pydantic.Field(gt=0)
    safety_margin_pct: float = pydantic.Field(ge=0.0, lt=1.0)


class CoolingCapacity(pydantic.BaseModel):
    """Cooling limits supplied by the operator."""

    capacity_kw: float = pydantic.Field(gt=0)
    safety_margin_pct: float = pydantic.Field(ge=0.0, lt=1.0)
    heat_conversion_factor: float = pydantic.Field(
        default=DEFAULT_HEAT_CONVERSION_FACTOR, gt=0.0
    )


class ElectricalState(pydantic.BaseModel):
    """Current or predicted electrical state."""

    it_load_kw: float
    pue: PUEResult
    facility_power_kw: float
    capacity_kw: float
    safety_margin_pct: float
    physical_headroom_kw: float
    safety_adjusted_headroom_kw: float


class CoolingState(pydantic.BaseModel):
    """Current or predicted cooling state."""

    it_load_kw: float
    heat_conversion_factor: float
    heat_load_kw: float
    capacity_kw: float
    safety_margin_pct: float
    physical_headroom_kw: float
    safety_adjusted_headroom_kw: float


class FacilityState(pydantic.BaseModel):
    """Combined electrical and cooling state at one point in time."""

    electrical: ElectricalState
    cooling: CoolingState


class CalculationReport(pydantic.BaseModel):
    """Full engineering report for a proposed set of changes."""

    current: FacilityState
    predicted: FacilityState
    changes: list[LoadChange]
    assumptions: list[str]
    formula_log: dict[str, str]


def _round_kw(value: float) -> float:
    """Round a kW value for presentation (internal math keeps full float)."""
    return round(value, 4)


def _round_pue(value: float) -> float:
    """Round a PUE value for presentation."""
    return round(value, 4)


def facility_power_kw(it_load_kw: float, pue_value: float) -> float:
    """Canonical facility power: ``it_load_kw * pue_value`` (see ``FORMULAS``)."""
    return it_load_kw * pue_value


def predicted_load_kw(current_kw: float, delta_kw: float) -> float:
    """Canonical predicted load: ``current + delta``. May be negative.

    Callers decide how to treat a negative result (the decision engine rejects
    it as an invalid proposal rather than clamping).
    """
    return current_kw + delta_kw


def calculate_electrical(
    it_load_kw: float,
    pue: PUEResult,
    capacity: ElectricalCapacity,
) -> ElectricalState:
    """Calculate facility power and electrical headroom.

    Formulas:
        facility_power_kw = it_load_kw * pue_value
        physical_headroom_kw = capacity_kw - facility_power_kw
        safety_adjusted_headroom_kw = capacity_kw * (1 - safety_margin_pct)
            - facility_power_kw

    Negative headroom means the load exceeds capacity; it is reported as-is
    rather than clamped, so callers can act on the overload.
    """
    facility_power_kw_value = facility_power_kw(it_load_kw, pue.value)
    physical_headroom_kw = capacity.capacity_kw - facility_power_kw_value
    usable_capacity_kw = capacity.capacity_kw * (1.0 - capacity.safety_margin_pct)
    safety_adjusted_headroom_kw = usable_capacity_kw - facility_power_kw_value
    return ElectricalState(
        it_load_kw=_round_kw(it_load_kw),
        pue=PUEResult(
            value=_round_pue(pue.value), source=pue.source, notes=pue.notes
        ),
        facility_power_kw=_round_kw(facility_power_kw_value),
        capacity_kw=_round_kw(capacity.capacity_kw),
        safety_margin_pct=capacity.safety_margin_pct,
        physical_headroom_kw=_round_kw(physical_headroom_kw),
        safety_adjusted_headroom_kw=_round_kw(safety_adjusted_headroom_kw),
    )


def calculate_cooling(
    it_load_kw: float,
    capacity: CoolingCapacity,
) -> CoolingState:
    """Calculate heat load and cooling headroom.

    Formulas:
        heat_load_kw = it_load_kw * heat_conversion_factor
        physical_headroom_kw = capacity_kw - heat_load_kw
        safety_adjusted_headroom_kw = capacity_kw * (1 - safety_margin_pct)
            - heat_load_kw

    The default heat_conversion_factor is 1.0, encoding the approximation
    that IT electrical load equals IT heat output. A future thermal model
    can override it per call without changing this module.
    """
    heat_load_kw = it_load_kw * capacity.heat_conversion_factor
    physical_headroom_kw = capacity.capacity_kw - heat_load_kw
    usable_capacity_kw = capacity.capacity_kw * (1.0 - capacity.safety_margin_pct)
    safety_adjusted_headroom_kw = usable_capacity_kw - heat_load_kw
    return CoolingState(
        it_load_kw=_round_kw(it_load_kw),
        heat_conversion_factor=capacity.heat_conversion_factor,
        heat_load_kw=_round_kw(heat_load_kw),
        capacity_kw=_round_kw(capacity.capacity_kw),
        safety_margin_pct=capacity.safety_margin_pct,
        physical_headroom_kw=_round_kw(physical_headroom_kw),
        safety_adjusted_headroom_kw=_round_kw(safety_adjusted_headroom_kw),
    )


def compute_current_state(
    it_load_kw: float,
    pue: PUEResult,
    electrical_capacity: ElectricalCapacity,
    cooling_capacity: CoolingCapacity,
) -> FacilityState:
    """Current-state snapshot from IT load and PUE."""
    if it_load_kw < 0:
        msg = f"it_load_kw must be >= 0 (got {it_load_kw})"
        raise ValueError(msg)
    return FacilityState(
        electrical=calculate_electrical(it_load_kw, pue, electrical_capacity),
        cooling=calculate_cooling(it_load_kw, cooling_capacity),
    )


def _aggregate_delta(changes: list[LoadChange]) -> float:
    """Sum the IT-load deltas of multiple proposed changes."""
    total = 0.0
    for change in changes:
        total += change.it_load_delta_kw
    return total


def compute_predicted_state(
    current_state: FacilityState,
    changes: list[LoadChange],
    pue: PUEResult,
    electrical_capacity: ElectricalCapacity,
    cooling_capacity: CoolingCapacity,
) -> FacilityState:
    """Predicted state after applying a list of proposed changes.

    Multiple changes are aggregated into a single delta first, then applied.
    Combined changes that would drive IT load negative are rejected.
    """
    delta_kw = _aggregate_delta(changes)
    predicted_it_load_kw = current_state.electrical.it_load_kw + delta_kw
    if predicted_it_load_kw < 0:
        msg = (
            "Combined changes would drive IT load negative "
            f"({current_state.electrical.it_load_kw} + {delta_kw} = "
            f"{predicted_it_load_kw} kW)"
        )
        raise ValueError(msg)
    return FacilityState(
        electrical=calculate_electrical(
            predicted_it_load_kw, pue, electrical_capacity
        ),
        cooling=calculate_cooling(predicted_it_load_kw, cooling_capacity),
    )


def build_report(
    it_load_kw: float,
    pue: PUEResult,
    changes: list[LoadChange],
    electrical_capacity: ElectricalCapacity,
    cooling_capacity: CoolingCapacity,
) -> CalculationReport:
    """Full report: current state, predicted state, changes, assumptions, formulas."""
    current = compute_current_state(
        it_load_kw, pue, electrical_capacity, cooling_capacity
    )
    predicted = compute_predicted_state(
        current, changes, pue, electrical_capacity, cooling_capacity
    )
    formula_log = {
        "facility_power_kw": "facility_power_kw = it_load_kw * pue_value",
        "physical_headroom_kw": (
            "physical_headroom_kw = capacity_kw - facility_power_kw"
        ),
        "safety_adjusted_headroom_kw": (
            "safety_adjusted_headroom_kw = capacity_kw * (1 - safety_margin_pct)"
            " - facility_power_kw"
        ),
        "heat_load_kw": "heat_load_kw = it_load_kw * heat_conversion_factor",
        "predicted_it_load_kw": (
            "predicted_it_load_kw = current_it_load_kw"
            " + sum(change.it_load_delta_kw)"
        ),
    }
    assumptions = [
        MODELING_NOTES["it_heat_approximation"],
        MODELING_NOTES["pue_source"],
        MODELING_NOTES["future_extensibility"],
        f"PUE source: {pue.source.value}",
        f"heat_conversion_factor: {cooling_capacity.heat_conversion_factor}",
        f"electrical safety margin: {electrical_capacity.safety_margin_pct * 100:.1f}%",
        f"cooling safety margin: {cooling_capacity.safety_margin_pct * 100:.1f}%",
    ]
    return CalculationReport(
        current=current,
        predicted=predicted,
        changes=changes,
        assumptions=assumptions,
        formula_log=formula_log,
    )
