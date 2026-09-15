"""Owner-scoped research preferences; execution enforcement is a separate gate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
Money = Annotated[Decimal, Field(gt=0, max_digits=16, decimal_places=6, allow_inf_nan=False)]
PositiveCount = Annotated[int, Field(gt=0, strict=True)]
Service = Literal["site-audit", "company-intelligence"]


class ResearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    mission_timeout_seconds: PositiveSeconds | None = None
    request_timeout_seconds: PositiveSeconds = 20
    llm_call_timeout_seconds: PositiveSeconds = 60
    progress_warning_seconds: PositiveSeconds = 45
    currency: Literal["RUB", "USD", "EUR"] = "RUB"
    cost_warning_amount: Money | None = None
    cost_limit_amount: Money | None = None
    token_warning: PositiveCount | None = None
    token_limit: PositiveCount | None = None
    threshold_action: Literal["notify_continue", "pause"] = "notify_continue"
    hard_limit_action: Literal["pause", "stop"] = "pause"
    retry_count: Annotated[int, Field(ge=0, strict=True)] = 2
    retry_backoff_seconds: PositiveSeconds = 2
    unknown_price_action: Literal["pause", "allow_unpriced"] = "pause"

    @model_validator(mode="after")
    def validate_thresholds(self):
        for warning, limit in ((self.cost_warning_amount, self.cost_limit_amount),
                               (self.token_warning, self.token_limit)):
            if warning is not None and limit is not None and warning > limit:
                raise ValueError("warning_exceeds_limit")
        if self.cost_limit_amount is not None and self.unknown_price_action == "allow_unpriced":
            raise ValueError("hard_cost_limit_requires_known_price")
        return self


class SettingsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: int
    service: Service
    revision: int
    updated_at: str | None
    settings: ResearchSettings
    # Do not imply saving a preference changes running/provider behaviour.
    execution_enabled: Literal[False] = False


class SettingsConflict(ValueError):
    pass


class ResearchSettingsRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS user_research_settings (
                owner_id INTEGER NOT NULL, service TEXT NOT NULL, revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(owner_id, service, revision))""")
            db.execute("""CREATE TABLE IF NOT EXISTS research_settings_snapshots (
                owner_id INTEGER NOT NULL, mission_id TEXT NOT NULL, run_id TEXT NOT NULL,
                payload TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(owner_id, mission_id, run_id))""")

    @staticmethod
    def _read(db, owner_id: int, service: Service) -> SettingsRecord:
        row = db.execute("""SELECT revision, updated_at, payload FROM user_research_settings
            WHERE owner_id=? AND service=? ORDER BY revision DESC LIMIT 1""", (owner_id, service)).fetchone()
        return SettingsRecord(owner_id=owner_id, service=service, revision=row[0] if row else 0,
                              updated_at=row[1] if row else None,
                              settings=ResearchSettings.model_validate_json(row[2]) if row else ResearchSettings())

    def get(self, owner_id: int, service: Service) -> SettingsRecord:
        with sqlite3.connect(self.path) as db:
            return self._read(db, owner_id, service)

    def save(self, owner_id: int, service: Service, settings: ResearchSettings,
             *, expected_revision: int) -> SettingsRecord:
        # Revalidate even if an internal caller used model_construct/model_copy.
        settings = ResearchSettings.model_validate(settings.model_dump())
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db, owner_id, service)
            if expected_revision != current.revision:
                raise SettingsConflict("settings_revision_conflict")
            now = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO user_research_settings VALUES (?, ?, ?, ?, ?)",
                       (owner_id, service, current.revision + 1, now, settings.model_dump_json()))
            return self._read(db, owner_id, service)

    def snapshot(self, owner_id: int, service: Service, mission_id: str, run_id: str,
                 *, overrides: dict | None = None) -> dict:
        """Internal operation: caller must resolve mission ownership before use.

        No public snapshot API until run ownership/enforcement is connected.
        Existing snapshots are immutable, including on idempotent retries.
        """
        if not mission_id or not run_id:
            raise ValueError("mission_and_run_required")
        # Decimal money and JSON input must produce the same persisted override.
        overrides = json.loads(json.dumps(overrides or {}, default=str, allow_nan=False))
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("""SELECT payload, digest FROM research_settings_snapshots
                WHERE owner_id=? AND mission_id=? AND run_id=?""", (owner_id, mission_id, run_id)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if saved["service"] != service or saved["overrides"] != (overrides or {}):
                    raise SettingsConflict("run_snapshot_conflict")
                return {**saved, "digest": previous[1]}
            record = self._read(db, owner_id, service)
            selected = ResearchSettings.model_validate({**record.settings.model_dump(), **(overrides or {})})
            payload = {"owner_id": owner_id, "service": service, "mission_id": mission_id,
                       "run_id": run_id, "settings_revision": record.revision,
                       "created_at": datetime.now(UTC).isoformat(), "overrides": overrides or {},
                       "requested": selected.model_dump(mode="json"),
                       "effective": None, "enforcement_state": "pending_integration"}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
            db.execute("INSERT INTO research_settings_snapshots VALUES (?, ?, ?, ?, ?)",
                       (owner_id, mission_id, run_id, encoded, digest))
            return {**payload, "digest": digest}
