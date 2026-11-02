# Copyright 2026 Lightibulb

"""Entity auto-creation from CSV inputs."""

import csv
import io
import sqlite3

from ecofac.ingestion.pipeline import normalize_headers

MAX_AUTO_ENTITIES = 500
MAX_ID_LENGTH = 100
ID_PATTERN = r"^[A-Za-z0-9._:-]+$"


def extract_entity_pairs(text: str) -> set[tuple[str, str]]:
    """Collect well-formed (building_id, meter_id) pairs from CSV text."""
    pairs: set[tuple[str, str]] = set()
    try:
        reader = csv.DictReader(io.StringIO(text))
        normalize_headers(reader)
        for row in reader:
            building = (row.get("building_id") or "").strip()
            meter = (row.get("meter_id") or "").strip()
            if building and meter and max(len(building), len(meter)) <= MAX_ID_LENGTH:
                pairs.add((building, meter))
            if len(pairs) > MAX_AUTO_ENTITIES:
                msg = f"CSV references more than {MAX_AUTO_ENTITIES} meters"
                raise ValueError(
                    msg
                )
    except csv.Error:
        return set()
    return pairs


def create_entities(
    conn: sqlite3.Connection, pairs: set[tuple[str, str]], now: str
) -> None:
    """Auto-create the organization, buildings and meters a CSV refers to."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations (id, name, created_at) "
        "VALUES ('o-auto', 'Auto Org', ?)",
        (now,),
    )
    for building_id, meter_id in sorted(pairs):
        conn.execute(
            "INSERT OR IGNORE INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o-auto', ?, ?)",
            (building_id, building_id, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meters (id, building_id, fuel_type, unit, created_at) "
            "VALUES (?, ?, 'elec', 'kWh', ?)",
            (meter_id, building_id, now),
        )
