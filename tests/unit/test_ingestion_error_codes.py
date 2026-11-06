# P2-6: ingestion error-code drift test.
#
# pipeline.py raises IngestionError with string codes. imports.py maps
# those codes to HTTP statuses via _INGESTION_STATUS; any unmapped code
# silently falls back to HTTP 400. If pipeline.py adds a code and
# imports.py is not updated, the new code degrades to 400 with no test
# failure. This test AST-parses both files and asserts:
#   1. every code raised in pipeline.py is mapped in _INGESTION_STATUS
#      or listed in DOCUMENTED_400_CODES
#   2. every entry in _INGESTION_STATUS can actually be raised
#   3. every entry in DOCUMENTED_400_CODES can actually be raised

import ast
from pathlib import Path

PIPELINE_PATH = Path("src/ecofac/ingestion/pipeline.py")
IMPORTS_PATH = Path("src/ecofac/api/routes/imports.py")

# Codes intentionally mapped to the 400 fallback. Adding here is a
# deliberate choice; document why before adding.
DOCUMENTED_400_CODES = frozenset(
    {
        "malformed_csv",
        "missing_columns",
    }
)


def _pipeline_codes() -> set[str]:
    src = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "IngestionError"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            codes.add(first.value)
    return codes


def _ingestion_status_map() -> dict[str, int]:
    src = IMPORTS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "_INGESTION_STATUS":
                value = ast.literal_eval(node.value)
                if not isinstance(value, dict):
                    msg = (
                        f"_INGESTION_STATUS in {IMPORTS_PATH} is not a "
                        f"literal dict (got {type(value).__name__})"
                    )
                    raise AssertionError(msg)
                return value
    msg = f"_INGESTION_STATUS assignment not found in {IMPORTS_PATH}"
    raise AssertionError(msg)


def test_every_pipeline_code_is_mapped_or_documented() -> None:
    codes = _pipeline_codes()
    mapped = _ingestion_status_map()
    undocumented = codes - mapped.keys() - DOCUMENTED_400_CODES
    msg = (
        f"pipeline.py raises codes not mapped in _INGESTION_STATUS and not "
        f"in DOCUMENTED_400_CODES: {sorted(undocumented)}. "
        f"Add each to imports._INGESTION_STATUS (with an HTTP status) or "
        f"to DOCUMENTED_400_CODES (defaults to 400)."
    )
    assert not undocumented, msg


def test_no_dead_entries_in_ingestion_status_map() -> None:
    codes = _pipeline_codes()
    mapped = _ingestion_status_map()
    dead = mapped.keys() - codes
    msg = f"_INGESTION_STATUS has entries pipeline.py never raises: {sorted(dead)}. Remove them."
    assert not dead, msg


def test_documented_400_codes_are_real() -> None:
    codes = _pipeline_codes()
    extra = DOCUMENTED_400_CODES - codes
    msg = (
        f"DOCUMENTED_400_CODES lists codes pipeline.py does not raise: "
        f"{sorted(extra)}. Remove them."
    )
    assert not extra, msg
