# Copyright 2026 Lightibulb

"""Deployment readiness check.

Verifies, in a fresh process:
1. FastAPI app imports and serves /health via TestClient.
2. Fresh file-backed SQLite DB initializes with the full schema.
3. OpenAPI schema generates without error.
4. Required routes are present.

Exits 0 on success, non-zero on failure. Safe to run in CI or locally.
"""

import pathlib
import sys
import tempfile

# Add project root so 'ecofac' resolves even when invoked as a script.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> int:
    """Run the preflight checks. Return process exit code."""
    print("PREFLIGHT: importing app...")
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from ecofac.main import app  # noqa: PLC0415

    print("PREFLIGHT: booting TestClient...")
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != 200:
            print(f"FAIL: /health returned {response.status_code}")
            return 1
        if response.json() != {"status": "ok"}:
            print(f"FAIL: /health body unexpected: {response.json()}")
            return 1

    print("PREFLIGHT: initializing fresh SQLite file DB...")
    from ecofac.persistence.database import get_connection, init_db  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = pathlib.Path(tmpdir) / "preflight.db"
        conn = get_connection(str(db_path))
        init_db(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "organizations",
            "buildings",
            "meters",
            "energy_readings",
            "anomalies",
            "incidents",
            "evidence_records",
            "audit_events",
        }
        missing = required - tables
        if missing:
            print(f"FAIL: missing tables: {missing}")
            conn.close()
            return 1
        conn.close()

    print("PREFLIGHT: generating OpenAPI schema...")
    schema = app.openapi()
    if "/health" not in schema.get("paths", {}):
        print("FAIL: /health missing from OpenAPI schema")
        return 1
    if len(schema.get("paths", {})) < 5:
        print(f"FAIL: only {len(schema.get('paths', {}))} paths registered")
        return 1

    print("PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
