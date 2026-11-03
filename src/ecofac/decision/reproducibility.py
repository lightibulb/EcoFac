# Copyright 2026 Lightibulb
"""Reproducibility captures for transparent decisions.

A ``ReproductionFingerprint`` stores:
  - The engine version and configuration version that produced the decision
  - SHA-256 hashes of the canonical (sorted-key) JSON of inputs and config
  - The full evidence bundle, so the decision can be replayed

Two identical requests against identical configurations produce identical
``input_fingerprint`` and ``config_fingerprint`` values. This is what allows
a historical decision to be independently re-verified.
"""

import hashlib
import json

import pydantic


class ReproductionFingerprint(pydantic.BaseModel):
    """Everything needed to reproduce a decision."""

    engine_version: str
    config_version: str
    input_fingerprint: str
    config_fingerprint: str
    decision_timestamp: str
    full_bundle: dict


def _canonical_json(payload: dict) -> str:
    """Canonical JSON: sorted keys, compact separators, str fallback."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_fingerprint(payload: dict) -> str:
    """Return a stable SHA-256 hex digest of ``payload``."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_fingerprint(
    *,
    engine_version: str,
    config_version: str,
    input_payload: dict,
    config_payload: dict,
    decision_timestamp: str,
    full_bundle: dict,
) -> ReproductionFingerprint:
    """Build a reproducibility fingerprint for one decision."""
    return ReproductionFingerprint(
        engine_version=engine_version,
        config_version=config_version,
        input_fingerprint=compute_fingerprint(input_payload),
        config_fingerprint=compute_fingerprint(config_payload),
        decision_timestamp=decision_timestamp,
        full_bundle=full_bundle,
    )


def verify_reproduction(
    fingerprint: ReproductionFingerprint,
    *,
    input_payload: dict,
    config_payload: dict,
) -> bool:
    """Return True if the fingerprint matches the supplied payloads."""
    return (
        compute_fingerprint(input_payload) == fingerprint.input_fingerprint
        and compute_fingerprint(config_payload) == fingerprint.config_fingerprint
    )
