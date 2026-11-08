# Copyright 2026 Lightibulb

"""World "test drive": fixed, explicit, hypothetical engine evaluations.

A test drive answers one question: *given this hypothetical facility and this
proposed load change, what does the canonical decision engine say?* It is
deliberately independent of imported meter data:

* Meters report **energy per interval (kWh)**, which is not power (kW); and
  readings from unrelated meters cannot be summed into a facility total. The
  previous test drive averaged the latest kWh readings of every meter and fed
  the result in as kW. That was wrong twice over, so meter data is not used.
* Every input here is a stated, labelled hypothetical value (``source =
  ASSUMED``). Nothing is measured, and nothing is derived silently: the cooling
  load is *stated* (equal to the IT load, the documented heat-factor-1.0
  simplification), never computed by the engine.
* The evaluation is **pure**: it reads and writes no database state. It is not
  a decision, a scenario or an audit event unless the caller explicitly saves
  the returned ``reproduce.scenario_request`` through the Scenario API.

The presets are calibrated against ``FACILITY_CONFIG`` so each lands on a
different side of a real constraint boundary; ``calibration`` in the response
shows the exact thresholds. Expected outcomes are part of the preset definition
and are asserted by the regression tests (``tests/unit/test_world_testdrive.py``).
"""

import dataclasses
import functools

from ecofac.decision.calculations import FORMULAS
from ecofac.decision.limits import ConstraintConfig, CoolingLimits, ElectricalLimits
from ecofac.decision.measurements import MeasurementSource
from ecofac.decision.scenarios import build_transparent_request
from ecofac.decision.transparent import decide_transparently
from ecofac.timeutil import now_z

TEST_DRIVE_VERSION = "1"
CONFIG_VERSION = f"testdrive-v{TEST_DRIVE_VERSION}"

# Hypothetical facility limits. Not read from any real facility.
FACILITY_CONFIG = ConstraintConfig(
    electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
    cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
)

# Hypothetical operating point the presets start from.
BASELINE_IT_LOAD_KW = 500.0
BASELINE_COOLING_LOAD_KW = 500.0  # stated equal to IT load (heat factor 1.0)
BASELINE_PUE = 1.4

BASIS = {
    "it_load_kw": "Hypothetical baseline IT load stated by the test drive. "
    "Not read from any meter.",
    "cooling_load_kw": "Hypothetical cooling load, stated equal to the IT load "
    "(the documented heat-factor-1.0 simplification). Not measured and not "
    "derived by the engine.",
    "pue": "Hypothetical PUE, assumed. Not measured.",
    "proposed_it_delta_kw": "Preset change to IT load, as a percentage of the "
    "hypothetical baseline.",
    "proposed_cooling_delta_kw": "Stated equal to the IT delta (same "
    "simplification). Not derived by the engine.",
    "config": "Hypothetical facility limits. Not read from a real facility.",
}

NOTES = (
    (
        "Hypothetical evaluation: nothing was saved. It is not a decision, a "
        "scenario or an audit event."
    ),
    (
        "Imported meter data is not used: meters report energy per interval (kWh), "
        "which is not power (kW), and unrelated meters cannot be summed into a "
        "facility total."
    ),
    (
        "All inputs are stated hypothetical values (source ASSUMED); the engine "
        "records them as assumptions."
    ),
)


class UnknownPresetError(KeyError):
    """Raised for a preset id that is not defined."""


@dataclasses.dataclass(frozen=True)
class Preset:
    """One calibrated test drive."""

    id: str
    label: str
    description: str
    it_delta_kw: float
    expected_status: str
    expected_precedence: str

    @property
    def cooling_delta_kw(self) -> float:
        """Cooling delta is stated equal to the IT delta (see ``BASIS``)."""
        return self.it_delta_kw

    @property
    def it_delta_pct(self) -> float:
        return round(self.it_delta_kw / BASELINE_IT_LOAD_KW * 100.0, 4)


PRESETS: tuple[Preset, ...] = (
    Preset(
        id="baseline",
        label="No change",
        description="Baseline operating point, no proposed change.",
        it_delta_kw=0.0,
        expected_status="APPROVE",
        expected_precedence="clean",
    ),
    Preset(
        id="plus_10pct",
        label="+10% IT load",
        description="Modest increase; stays clear of the warning band.",
        it_delta_kw=50.0,
        expected_status="APPROVE",
        expected_precedence="clean",
    ),
    Preset(
        id="plus_20pct",
        label="+20% IT load",
        description="Electrical headroom drops inside the warning band.",
        it_delta_kw=100.0,
        expected_status="REVIEW",
        expected_precedence="near_threshold",
    ),
    Preset(
        id="plus_40pct",
        label="+40% IT load",
        description="Facility power exceeds the permitted (reserve-adjusted) limit.",
        it_delta_kw=200.0,
        expected_status="REJECT",
        expected_precedence="reserve_violation",
    ),
    Preset(
        id="plus60",
        label="+60% IT load",
        description="Proposed +60% IT load with matching cooling.",
        it_delta_kw=BASELINE_IT_LOAD_KW * 0.60,
        expected_status="REJECT",
        expected_precedence="hard_limit_violation",
    ),
# LMF-P2-PRESETS-APPLIED
    Preset(
        id="plus_60pct",
        label="+60% IT load",
        description="Facility power exceeds the physical electrical capacity.",
        it_delta_kw=300.0,
        expected_status="REJECT",
        expected_precedence="hard_limit_violation",
    ),
)
_BY_ID = {p.id: p for p in PRESETS}
_PRESET_ALIASES = {"plus40": "plus_40pct"}  # backward-compat alias


def get_preset(preset_id: str) -> Preset:
    """Return the preset or raise ``UnknownPresetError``."""
    try:
        return _BY_ID[preset_id]
    except KeyError as exc:
        raise UnknownPresetError(preset_id) from exc


def _calibration() -> dict:
    """Exact thresholds the presets are calibrated against (kW)."""
    out = {}
    for name, limits in (
        ("electrical", FACILITY_CONFIG.electrical),
        ("cooling", FACILITY_CONFIG.cooling),
    ):
        permitted = limits.capacity * (1.0 - limits.reserve_pct)
        out[name] = {
            "capacity_kw": limits.capacity,
            "reserve_pct": limits.reserve_pct,
            "permitted_kw": round(permitted, 4),
            "warning_above_kw": round(permitted * (1.0 - limits.warning_threshold_pct), 4),
            "note": "WARNING above warning_above_kw; RESERVE_VIOLATION above "
            "permitted_kw; PHYSICAL_VIOLATION above capacity_kw.",
        }
    out["electrical"]["observed_quantity"] = "facility power = IT load x PUE"
    out["cooling"]["observed_quantity"] = "stated cooling load"
    return out


def _inputs(preset: Preset) -> dict:
    """The exact inputs the engine receives, each with its label."""
    source = MeasurementSource.ASSUMED.value
    return {
        "it_load_kw": {
            "value": BASELINE_IT_LOAD_KW,
            "unit": "kW",
            "source": source,
            "basis": BASIS["it_load_kw"],
        },
        "cooling_load_kw": {
            "value": BASELINE_COOLING_LOAD_KW,
            "unit": "kW",
            "source": source,
            "basis": BASIS["cooling_load_kw"],
        },
        "pue": {
            "value": BASELINE_PUE,
            "unit": "dimensionless",
            "source": source,
            "basis": BASIS["pue"],
        },
        "proposed_it_delta_kw": {
            "value": preset.it_delta_kw,
            "unit": "kW",
            "source": "PRESET",
            "basis": BASIS["proposed_it_delta_kw"],
            "percent_of_baseline_it_load": preset.it_delta_pct,
        },
        "proposed_cooling_delta_kw": {
            "value": preset.cooling_delta_kw,
            "unit": "kW",
            "source": "PRESET",
            "basis": BASIS["proposed_cooling_delta_kw"],
        },
        "config": {
            "value": FACILITY_CONFIG.model_dump(mode="json"),
            "config_version": CONFIG_VERSION,
            "basis": BASIS["config"],
        },
    }


def reproduce_payload(preset: Preset) -> dict:
    """Body for ``POST /api/v1/scenarios/evaluate`` that reproduces this test drive.

    ``persist_as_decision`` is False: saving it creates a *scenario* (the
    hypothetical store), never an operational decision.
    """
    source = MeasurementSource.ASSUMED.value
    return {
        "name": f"Test drive: {preset.label}",
        "config": FACILITY_CONFIG.model_dump(mode="json"),
        "config_version": CONFIG_VERSION,
        "it_load_kw": BASELINE_IT_LOAD_KW,
        "cooling_load_kw": BASELINE_COOLING_LOAD_KW,
        "pue": BASELINE_PUE,
        "proposed_it_delta_kw": preset.it_delta_kw,
        "proposed_cooling_delta_kw": preset.cooling_delta_kw,
        "it_source": source,
        "cooling_source": source,
        "pue_source": source,
        "persist_as_decision": False,
    }


def list_presets() -> list[dict]:
    """Static description of every preset (no engine run, no database)."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "proposed_it_delta_kw": p.it_delta_kw,
            "proposed_cooling_delta_kw": p.cooling_delta_kw,
            "percent_of_baseline_it_load": p.it_delta_pct,
            "expected": {"status": p.expected_status, "precedence": p.expected_precedence},
        }
        for p in PRESETS
    ]


@functools.lru_cache(maxsize=32)
def _compute_test_drive(canonical_preset_id: str) -> dict:
    """Compute the test-drive payload minus the fresh ``evaluated_at`` stamp.

    Pure and deterministic for a given canonical preset id. Cached so a
    page reload does not re-run the decision engine (each call performs
    hundreds of constraint evaluations). The ``evaluated_at`` field is a
    placeholder; ``run_test_drive`` overwrites it on every call.

    Raises ``UnknownPresetError`` for an unknown id (``functools.lru_cache``
    does not cache exceptions, so repeated bad ids re-raise).
    """
    preset = get_preset(canonical_preset_id)
    request = build_transparent_request(
        it_load_kw=BASELINE_IT_LOAD_KW,
        cooling_load_kw=BASELINE_COOLING_LOAD_KW,
        pue=BASELINE_PUE,
        proposed_it_delta_kw=preset.it_delta_kw,
        proposed_cooling_delta_kw=preset.cooling_delta_kw,
        config=FACILITY_CONFIG,
        config_version=CONFIG_VERSION,
    )
    result = decide_transparently(request)
    decision = result.state_machine_decision
    calc = result.evidence.calculation_inputs

    # ``evaluated_constraints`` lists the two CURRENT-state evaluations first,
    # then the two PREDICTED-state ones (corrective-action mode is never used here).
    phases = ("current", "current", "predicted", "predicted")
    constraints = [
        {
            "phase": phase,
            "name": ev.name,
            "observed_kw": ev.observed,
            "permitted_kw": ev.permitted,
            "physical_capacity_kw": ev.physical_capacity,
            "headroom_to_permitted_kw": ev.headroom_to_permitted,
            "severity": ev.severity.value,
            "reason": ev.reason,
        }
        for phase, ev in zip(phases, decision.evaluated_constraints, strict=False)
    ]

    return {
        "kind": "test_drive",
        "version": TEST_DRIVE_VERSION,
        "mode": "HYPOTHETICAL",
        "persisted": False,
        "evaluated_at": "",  # overwritten per-call by run_test_drive
        "preset": {
            "id": preset.id,
            "label": preset.label,
            "description": preset.description,
        },
        "inputs": _inputs(preset),
        "calibration": _calibration(),
        "derived": {
            "current_facility_power_kw": round(calc["current_facility_power_kw"], 4),
            "predicted_it_load_kw": round(calc["predicted_it_load_kw"], 4),
            "predicted_facility_power_kw": round(calc["predicted_facility_power_kw"], 4),
            "predicted_cooling_load_kw": round(calc["predicted_cooling_load_kw"], 4),
            "formulas": {
                key: FORMULAS[key]
                for key in (
                    "facility_power_kw",
                    "predicted_it_load_kw",
                    "predicted_facility_power_kw",
                    "predicted_cooling_kw",
                    "permitted_kw",
                )
            },
        },
        "constraints": constraints,
        "result": {
            "status": result.status,
            "precedence": result.precedence,
            "reasons": list(decision.reasons),
            "engine_version": result.engine_version,
            "config_version": result.config_version,
            "input_fingerprint": result.fingerprint.input_fingerprint,
            "config_fingerprint": result.fingerprint.config_fingerprint,
            "advisories": [a for a in result.evidence.assumptions if a.startswith("advisory")],
        },
        "expected": {
            "status": preset.expected_status,
            "precedence": preset.expected_precedence,
        },
        "matches_expected": (
            result.status == preset.expected_status
            and result.precedence == preset.expected_precedence
        ),
        "reproduce": {
            "endpoint": "POST /api/v1/scenarios/evaluate",
            "scenario_request": reproduce_payload(preset),
            "note": "Saving this creates a scenario (hypothetical store), "
            "never an operational decision.",
        },
        "notes": list(NOTES),
    }


def run_test_drive(preset_id: str) -> dict:
    """Evaluate one preset through the canonical engine. Pure: no I/O.

    Repeated calls for the same preset reuse the cached computation; the
    ``evaluated_at`` field is re-stamped on every call. The returned dict
    shares nested values with the cache, so callers must treat it as
    read-only (FastAPI serialization is read-only by design).

    Raises ``UnknownPresetError`` for an unknown id.
    """
    canonical = _PRESET_ALIASES.get(preset_id, preset_id)
    payload = dict(_compute_test_drive(canonical))
    payload["evaluated_at"] = now_z()
    return payload


def reset_test_drive_cache() -> None:
    """Clear the test-drive computation cache. Used by tests only."""
    _compute_test_drive.cache_clear()


def test_drive_cache_info() -> dict:
    """Return cache statistics. Used by tests and ops monitoring."""
    info = _compute_test_drive.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "maxsize": info.maxsize,
        "currsize": info.currsize,
    }
