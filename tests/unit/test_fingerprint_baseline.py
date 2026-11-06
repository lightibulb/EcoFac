# P2-1 fingerprint baseline test.
#
# Locks in the fingerprint of six representative decision requests before
# and after the EvidenceInputs refactor. Fingerprints hash the evidence
# bundle, so any accidental arg swap that reaches the bundle changes the
# hash. This is the guard against subtle refactor bugs that the type
# system cannot catch (final_status <-> final_reason,
# engine_version <-> config_version).
#
# To regenerate after an intentional change to the evidence bundle
# structure (e.g., engine version bump that alters the bundle schema):
#     python3 scripts/capture_fingerprints.py --force

import json
from pathlib import Path

from _fingerprint_helpers import capture_fingerprints

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints.json"


def test_fingerprints_match_baseline():
    baseline = json.loads(FIXTURES.read_text())
    current = capture_fingerprints()

    missing = sorted(baseline.keys() - current.keys())
    extra = sorted(current.keys() - baseline.keys())
    assert not missing, "requests removed from baseline: " + repr(missing)
    assert not extra, "new requests not in baseline: " + repr(extra)

    for name in sorted(baseline):
        assert current[name] == baseline[name], (
            "fingerprint drift for "
            + name
            + ": baseline="
            + repr(baseline[name])
            + " current="
            + repr(current[name])
            + ". If the evidence bundle schema intentionally changed, "
            + "regenerate with: python3 scripts/capture_fingerprints.py --force"
        )
