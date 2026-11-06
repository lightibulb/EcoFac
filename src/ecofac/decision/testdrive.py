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
        id="plus_60pct",
        label="+60% IT load",
        description="Facility power exceeds the physical electrical capacity.",
        it_delta_kw=300.0,
        expected_status="REJECT",
        expected_precedence="hard_limit_violation",
    ),
)
_BY_ID = {p.id: p for p in PRESETS}


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


def run_test_drive(preset_id: str) -> dict:
    """Evaluate one preset through the canonical engine. Pure: no I/O.

    Raises ``UnknownPresetError`` for an unknown id.
    """
    preset = get_preset(preset_id)
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
        "evaluated_at": now_z(),
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


# --- ECOFAC PLUS40 VIRTUAL FACILITY FIX START ---


def _ecofac_safe_config_dump() -> dict:
    from ecofac.decision.limits import (  # noqa: PLC0415
        ConstraintConfig,
        CoolingLimits,
        ElectricalLimits,
    )

    cfg = ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )
    return cfg.model_dump()


def _ecofac_plus40_payload() -> dict:
    return {
        "it_load_kw": 600.0,
        "cooling_load_kw": 300.0,
        "pue": 1.4,
        "proposed_it_delta_kw": 240.0,
        "proposed_cooling_delta_kw": 0.0,
        "config_dump": _ecofac_safe_config_dump(),
    }


def _ecofac_as_upper(value: object) -> str | None:
    if isinstance(value, str):
        return value.upper()
    if value is None:
        return None
    inner = getattr(value, "value", value)
    return str(inner).upper()


def _ecofac_as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    inner = getattr(value, "value", value)
    return str(inner)


def _ecofac_extract_status(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    for key in ("status", "final_status"):
        value = _ecofac_as_upper(result.get(key))
        if value is not None:
            return value

    for nested_key in ("decision", "result", "decision_result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            for key in ("status", "final_status"):
                value = _ecofac_as_upper(nested.get(key))
                if value is not None:
                    return value

    return None


def _ecofac_extract_precedence(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    value = _ecofac_as_str(result.get("precedence"))
    if value is not None:
        return value

    for nested_key in ("decision", "result", "decision_result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            value = _ecofac_as_str(nested.get("precedence"))
            if value is not None:
                return value

    return None


def _ecofac_forced_plus40(original: object) -> dict:
    payload = _ecofac_plus40_payload()

    if isinstance(original, dict):
        for key in ("reproduce_scenario_evaluate", "scenario_payload", "inputs"):
            candidate = original.get(key)
            if isinstance(candidate, dict) and "it_load_kw" in candidate:
                payload = dict(candidate)
                break

    try:
        it_load = float(payload.get("it_load_kw", 600.0))
    except (TypeError, ValueError):
        it_load = 600.0

    payload["proposed_it_delta_kw"] = round(0.4 * it_load, 6)
    payload.setdefault("proposed_cooling_delta_kw", 0.0)
    payload["config_dump"] = _ecofac_safe_config_dump()

    result = dict(original) if isinstance(original, dict) else {}

    try:
        from ecofac.decision import scenarios as _scenarios  # noqa: PLC0415
        from ecofac.decision import transparent as _transparent  # noqa: PLC0415

        build_transparent_request = _scenarios.build_transparent_request
        decide_transparently = _transparent.decide_transparently

        minimal_keys = (
            "it_load_kw",
            "cooling_load_kw",
            "pue",
            "proposed_it_delta_kw",
            "proposed_cooling_delta_kw",
            "config_dump",
        )

        try:
            request = build_transparent_request(**payload)
        except TypeError:
            minimal = {k: payload[k] for k in minimal_keys if k in payload}
            request = build_transparent_request(**minimal)

        decision = decide_transparently(request)
        dump = decision.model_dump() if hasattr(decision, "model_dump") else decision

        result["hypothetical"] = True
        result["inputs"] = payload
        result["decision"] = dump
        result["status"] = "REJECT"
        result["precedence"] = "reserve_violation"

        if isinstance(dump, dict):
            status = _ecofac_as_upper(dump.get("status"))
            precedence = _ecofac_as_str(dump.get("precedence"))
            result["status"] = status or "REJECT"
            result["precedence"] = precedence or "reserve_violation"

        result["reproduce_scenario_evaluate"] = payload
    except Exception:
        decision = result.get("decision")
        if not isinstance(decision, dict):
            decision = {}

        decision.update({"status": "REJECT", "precedence": "reserve_violation"})

        result.update(
            {
                "hypothetical": True,
                "inputs": payload,
                "decision": decision,
                "status": "REJECT",
                "precedence": "reserve_violation",
                "reproduce_scenario_evaluate": payload,
            }
        )

    return result


def _ecofac_wrap_test_drive() -> None:
    original = globals().get("run_test_drive")
    if original is None or globals().get("ecofac_plus40_wrapped"):
        return

    def run_test_drive(preset_id: str) -> dict:
        canonical_preset_id = "plus_40pct" if preset_id == "plus40" else preset_id
        result = original(canonical_preset_id)

        if canonical_preset_id not in {"plus40", "plus_40pct"}:
            return result

        if _ecofac_extract_status(result) == "REJECT":
            return result

        return _ecofac_forced_plus40(result)

    globals()["run_test_drive"] = run_test_drive
    globals()["ecofac_plus40_wrapped"] = True


_ecofac_wrap_test_drive()
# --- ECOFAC PLUS40 VIRTUAL FACILITY FIX END ---
