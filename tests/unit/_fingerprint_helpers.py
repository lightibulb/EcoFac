# Shared helper for P2-1 fingerprint baseline test and capture script.
#
# The fingerprint hashes the evidence bundle. If a refactor accidentally
# swaps two same-typed fields (final_status <-> final_reason,
# engine_version <-> config_version), the fingerprint changes and the
# baseline test fails. This is the safety net.
#
# Both capture_fingerprints.py (scripts/) and test_fingerprint_baseline.py
# (tests/unit/) import from here, so the request matrix is defined once.


from ecofac.decision import state_machine, transparent
from ecofac.decision.limits import ConstraintConfig, CoolingLimits, ElectricalLimits
from ecofac.decision.measurements import MeasurementSource, RichMeasurement
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    decide_transparently,
)

FIXED_TS = "2026-01-01T00:00:00.000000Z"


def _measurement(name, value, unit, tol=None):
    # Try several plausible tolerance field names. Works on the actual
    # schema regardless of the exact field name chosen upstream.
    base = {
        "name": name,
        "value": value,
        "unit": unit,
        "source": MeasurementSource.MEASURED,
    }
    if tol is None:
        return RichMeasurement(**base)
    candidates = [
        {"tolerance_abs": tol},
        {"tolerance": tol},
        {"absolute_tolerance": tol},
        {"tolerance_absolute": tol},
        {"uncertainty": {"absolute": tol}},
        {"uncertainty": {"absolute_tolerance": tol}},
        {"uncertainty": {"tolerance_abs": tol}},
    ]
    tried = []
    for kw in candidates:
        tried.append(list(kw.keys()))
        try:
            m = RichMeasurement(**base, **kw)
        except (TypeError, ValueError):
            continue
        if m.band() is not None:
            return m
    raise RuntimeError(
        "Cannot construct RichMeasurement with a tolerance that yields a "
        "non-None band(). Tried field names: " + repr(tried)
    )


def _config():
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=200.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=250.0, reserve_pct=0.10),
    )


def build_requests():
    # Six requests covering the branches the refactor touches:
    #   clean_approve     happy path
    #   reject_physical   PHYSICAL_VIOLATION
    #   review_quality    quality ineligible
    #   trusted_approve   TRUSTED mode, inputs_resolved_by_server
    #   invalid_proposal  negative predicted load
    #   missing_pue       PUE default path (is_default=True)
    return {
        "clean_approve": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-clean",
            it_load=_measurement("it_load", 80.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=_measurement("pue", 1.4, "dimensionless", 0.05),
            quality_eligible=True,
            mode=DecisionMode.HYPOTHETICAL,
            evaluation_time=FIXED_TS,
        ),
        "reject_physical": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-reject",
            it_load=_measurement("it_load", 200.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=_measurement("pue", 1.4, "dimensionless", 0.05),
            quality_eligible=True,
            mode=DecisionMode.HYPOTHETICAL,
            evaluation_time=FIXED_TS,
        ),
        "review_quality": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-review",
            it_load=_measurement("it_load", 80.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=_measurement("pue", 1.4, "dimensionless", 0.05),
            quality_eligible=False,
            quality_flags=["test_flag"],
            mode=DecisionMode.HYPOTHETICAL,
            evaluation_time=FIXED_TS,
        ),
        "trusted_approve": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-trusted",
            it_load=_measurement("it_load", 80.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=_measurement("pue", 1.4, "dimensionless", 0.05),
            quality_eligible=True,
            mode=DecisionMode.TRUSTED,
            inputs_resolved_by_server=True,
            evaluation_time=FIXED_TS,
        ),
        "invalid_proposal": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-invalid",
            it_load=_measurement("it_load", 80.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=_measurement("pue", 1.4, "dimensionless", 0.05),
            proposed_it_delta=-100.0,
            quality_eligible=True,
            mode=DecisionMode.HYPOTHETICAL,
            evaluation_time=FIXED_TS,
        ),
        "missing_pue": TransparentDecisionRequest(
            config=_config(),
            config_version="baseline-no-pue",
            it_load=_measurement("it_load", 80.0, "kW", 2.0),
            cooling_load=_measurement("cooling_load", 90.0, "kW", 2.0),
            pue=None,
            quality_eligible=True,
            mode=DecisionMode.HYPOTHETICAL,
            evaluation_time=FIXED_TS,
        ),
    }


def capture_fingerprints():
    # Mock both now_z references so fingerprints are fully deterministic.
    # decision_timestamp comes from state_machine._build_decision.now_z.
    # evaluation_time fallback (used only when request.evaluation_time is
    # None AND is_time_dependent is True) comes from transparent.now_z.
    # All requests here pass evaluation_time explicitly, so only the
    # state_machine path matters, but mock both for safety.
    original_sm_now = state_machine.now_z
    original_tr_now = transparent.now_z
    state_machine.now_z = lambda: FIXED_TS
    transparent.now_z = lambda: FIXED_TS
    try:
        result = {}
        for name, request in build_requests().items():
            decision = decide_transparently(request)
            result[name] = decision.fingerprint.model_dump(mode="json")
    finally:
        state_machine.now_z = original_sm_now
        transparent.now_z = original_tr_now
    return result
