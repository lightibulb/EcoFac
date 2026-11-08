# Copyright 2026 Lightibulb
"""Facility Operating Policy Manager.

Versioned, activatable operating-policy profiles controlling electrical and
cooling capacities, reserve percentages, warning thresholds and minimum
headroom. PUE band and freshness fields are reserved for the upcoming
PUE/freshness policies.
"""

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from ecofac.timeutil import now_z

_LEGACY_UPGRADES: dict[str, str] = {
    "electrical_warning_threshold_pct": "REAL NOT NULL DEFAULT 0.10",
    "electrical_min_headroom_kw": "REAL NOT NULL DEFAULT 0.0",
    "cooling_warning_threshold_pct": "REAL NOT NULL DEFAULT 0.10",
    "cooling_min_headroom_kw": "REAL NOT NULL DEFAULT 0.0",
    "pue_min": "REAL NOT NULL DEFAULT 1.0",
    "pue_max": "REAL NOT NULL DEFAULT 10.0",
    "freshness_max_age_seconds": "REAL NOT NULL DEFAULT 7200.0",
}


@dataclass
class FacilityPolicy:
    """A versioned facility operating policy profile."""

    id: str
    version: str
    created_at: str
    is_active: bool
    electrical_capacity_kw: float
    electrical_reserve_pct: float
    electrical_warning_threshold_pct: float
    electrical_min_headroom_kw: float
    cooling_capacity_kw: float
    cooling_reserve_pct: float
    cooling_warning_threshold_pct: float
    cooling_min_headroom_kw: float
    pue_min: float = 1.0
    pue_max: float = 10.0
    freshness_max_age_seconds: float = 7200.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the policy record."""
        return asdict(self)

    def config_snapshot(self) -> dict[str, Any]:
        """Canonical configuration snapshot (fingerprint input)."""
        return {
            "electrical": {
                "capacity": self.electrical_capacity_kw,
                "reserve_pct": self.electrical_reserve_pct,
                "warning_threshold_pct": self.electrical_warning_threshold_pct,
                "min_headroom": self.electrical_min_headroom_kw,
            },
            "cooling": {
                "capacity": self.cooling_capacity_kw,
                "reserve_pct": self.cooling_reserve_pct,
                "warning_threshold_pct": self.cooling_warning_threshold_pct,
                "min_headroom": self.cooling_min_headroom_kw,
            },
            "pue": {"min": self.pue_min, "max": self.pue_max},
            "freshness": {"max_age_seconds": self.freshness_max_age_seconds},
        }

    @property
    def fingerprint(self) -> str:
        """Stable configuration fingerprint for reproducibility records."""
        snapshot = self.config_snapshot()
        try:
            from ecofac.decision.reproducibility import (  # noqa: PLC0415
                compute_fingerprint,
            )

            return str(compute_fingerprint(snapshot))
        except Exception:
            raw = json.dumps(snapshot, sort_keys=True).encode("utf-8")
            return sha256(raw).hexdigest()[:16]

    def to_constraint_config(self) -> Any:
        """Build the decision-engine ConstraintConfig for this policy."""
        try:
            from ecofac.decision.limits import (  # noqa: PLC0415
                ConstraintConfig,
                CoolingLimits,
                ElectricalLimits,
            )
        except Exception:
            return None

        def _limits(
            cls: type, capacity: float, reserve: float,
            warning: float, headroom: float,
        ) -> Any:
            try:
                return cls(
                    capacity=capacity,
                    reserve_pct=reserve,
                    warning_threshold_pct=warning,
                    min_headroom=headroom,
                )
            except TypeError:
                return cls(capacity=capacity, reserve_pct=reserve)

        try:
            return ConstraintConfig(
                electrical=_limits(
                    ElectricalLimits,
                    self.electrical_capacity_kw,
                    self.electrical_reserve_pct,
                    self.electrical_warning_threshold_pct,
                    self.electrical_min_headroom_kw,
                ),
                cooling=_limits(
                    CoolingLimits,
                    self.cooling_capacity_kw,
                    self.cooling_reserve_pct,
                    self.cooling_warning_threshold_pct,
                    self.cooling_min_headroom_kw,
                ),
            )
        except Exception:
            return None


class PolicyManager:
    """Versioned store for facility operating policies."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create policy storage on first use (idempotent, self-migrating)."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS facility_policies (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                electrical_capacity_kw REAL NOT NULL,
                electrical_reserve_pct REAL NOT NULL,
                electrical_warning_threshold_pct REAL NOT NULL,
                electrical_min_headroom_kw REAL NOT NULL,
                cooling_capacity_kw REAL NOT NULL,
                cooling_reserve_pct REAL NOT NULL,
                cooling_warning_threshold_pct REAL NOT NULL,
                cooling_min_headroom_kw REAL NOT NULL,
                pue_min REAL NOT NULL DEFAULT 1.0,
                pue_max REAL NOT NULL DEFAULT 10.0,
                freshness_max_age_seconds REAL NOT NULL DEFAULT 7200.0,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_facility_policies_active
                ON facility_policies (is_active);
            """
        )
        known = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(facility_policies)")
        }
        for column, declaration in _LEGACY_UPGRADES.items():
            if column not in known:
                self.conn.execute(
                    "ALTER TABLE facility_policies ADD COLUMN "
                    + column + " " + declaration
                )
        self.conn.commit()

    def create_policy(self, policy: FacilityPolicy) -> str:
        """Insert a new policy version (raises IntegrityError on duplicate)."""
        self.conn.execute(
            """
            INSERT INTO facility_policies (
                id, version, created_at, is_active,
                electrical_capacity_kw, electrical_reserve_pct,
                electrical_warning_threshold_pct, electrical_min_headroom_kw,
                cooling_capacity_kw, cooling_reserve_pct,
                cooling_warning_threshold_pct, cooling_min_headroom_kw,
                pue_min, pue_max, freshness_max_age_seconds, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.id, policy.version, policy.created_at,
                1 if policy.is_active else 0,
                policy.electrical_capacity_kw, policy.electrical_reserve_pct,
                policy.electrical_warning_threshold_pct,
                policy.electrical_min_headroom_kw,
                policy.cooling_capacity_kw, policy.cooling_reserve_pct,
                policy.cooling_warning_threshold_pct,
                policy.cooling_min_headroom_kw,
                policy.pue_min, policy.pue_max,
                policy.freshness_max_age_seconds, policy.notes,
            ),
        )
        self.conn.commit()
        return policy.id

    def get_policy(self, policy_id: str) -> FacilityPolicy | None:
        """Fetch one policy by id."""
        row = self.conn.execute(
            "SELECT * FROM facility_policies WHERE id = ?", (policy_id,)
        ).fetchone()
        return self._row_to_policy(row) if row is not None else None

    def get_active_policy(self) -> FacilityPolicy | None:
        """Fetch the active policy, auto-creating the canonical default."""
        row = self.conn.execute(
            "SELECT * FROM facility_policies WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            return self._create_default_policy()
        return self._row_to_policy(row)

    def list_policies(self) -> list[FacilityPolicy]:
        """List all policies, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM facility_policies ORDER BY created_at DESC, id"
        ).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def activate_policy(self, policy_id: str) -> bool:
        """Activate one policy and deactivate all others."""
        self.conn.execute("UPDATE facility_policies SET is_active = 0")
        cursor = self.conn.execute(
            "UPDATE facility_policies SET is_active = 1 WHERE id = ?",
            (policy_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _create_default_policy(self) -> FacilityPolicy:
        policy = FacilityPolicy(
            id=str(uuid.uuid4()),
            version="default-v1",
            created_at=now_z(),
            is_active=True,
            electrical_capacity_kw=1000.0,
            electrical_reserve_pct=0.10,
            electrical_warning_threshold_pct=0.10,
            electrical_min_headroom_kw=0.0,
            cooling_capacity_kw=800.0,
            cooling_reserve_pct=0.10,
            cooling_warning_threshold_pct=0.10,
            cooling_min_headroom_kw=0.0,
            notes="Default operating policy (canonical demo configuration)",
        )
        self.create_policy(policy)
        return policy

    def _row_to_policy(self, row: sqlite3.Row) -> FacilityPolicy:
        return FacilityPolicy(
            id=row["id"],
            version=row["version"],
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
            electrical_capacity_kw=row["electrical_capacity_kw"],
            electrical_reserve_pct=row["electrical_reserve_pct"],
            electrical_warning_threshold_pct=row["electrical_warning_threshold_pct"],
            electrical_min_headroom_kw=row["electrical_min_headroom_kw"],
            cooling_capacity_kw=row["cooling_capacity_kw"],
            cooling_reserve_pct=row["cooling_reserve_pct"],
            cooling_warning_threshold_pct=row["cooling_warning_threshold_pct"],
            cooling_min_headroom_kw=row["cooling_min_headroom_kw"],
            pue_min=row["pue_min"],
            pue_max=row["pue_max"],
            freshness_max_age_seconds=row["freshness_max_age_seconds"],
            notes=row["notes"] or "",
        )
