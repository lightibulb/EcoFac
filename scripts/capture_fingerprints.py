# Capture the P2-1 fingerprint baseline. Run BEFORE the EvidenceInputs
# refactor. Refuses to overwrite an existing fixture; pass --force to
# recapture (e.g., after an engine-version bump).

import json
import pathlib
import sys

sys.path.insert(0, "tests/unit")

from _fingerprint_helpers import capture_fingerprints

FIXTURES = pathlib.Path("tests/unit/fixtures/fingerprints.json")


def main() -> int:
    if FIXTURES.exists() and "--force" not in sys.argv:
        print(
            str(FIXTURES) + " already exists; pass --force to overwrite",
            file=sys.stderr,
        )
        return 1
    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    data = capture_fingerprints()
    FIXTURES.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print("wrote " + str(FIXTURES) + " (" + str(len(data)) + " requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
