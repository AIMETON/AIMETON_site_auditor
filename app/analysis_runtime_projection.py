from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class AnalysisProjection:
    analysis_id: str
    mission_id: str
    source_url: str
    state: str
    phase: str
    created_at: str
    updated_at: str
    runtime_instance_id: str
    result: dict[str, Any] | None
    partial_result: dict[str, Any] | None = None


class AnalysisRuntimeProjectionStore:
    """Durable projection of the public async-analysis contract.

    The mission/orchestrator remains authoritative for mission semantics. This
    store only preserves the externally observable analysis id/state/events and
    result so a process restart does not turn an existing analysis into 404.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def migrate(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_analysis_projection (
                    analysis_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    runtime_instance_id TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_analysis_projection_mission
                    ON runtime_analysis_projection(mission_id);
                CREATE TABLE IF NOT EXISTS runtime_analysis_events (
                    analysis_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(analysis_id, sequence),
                    FOREIGN KEY(analysis_id)
                        REFERENCES runtime_analysis_projection(analysis_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_analysis_events_analysis
                    ON runtime_analysis_events(analysis_id, sequence);
                """
            )

            columns = {row[1] for row in db.execute("PRAGMA table_info(runtime_analysis_projection)")}
            if "partial_result_json" not in columns:
                db.execute("ALTER TABLE runtime_analysis_projection ADD COLUMN partial_result_json TEXT")

    def upsert(
        self,
        *,
        analysis_id: str,
        mission_id: str,
        source_url: str,
        state: str,
        phase: str,
        created_at: str,
        updated_at: str,
        runtime_instance_id: str,
        result: dict[str, Any] | None,
        partial_result: dict[str, Any] | None = None,
    ) -> None:
        rendered = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if result is not None
            else None
        )
        partial_rendered = json.dumps(partial_result, ensure_ascii=False) if partial_result is not None else None
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO runtime_analysis_projection(
                    analysis_id, mission_id, source_url, state, phase,
                    created_at, updated_at, runtime_instance_id, result_json, partial_result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    mission_id=excluded.mission_id,
                    source_url=excluded.source_url,
                    state=excluded.state,
                    phase=excluded.phase,
                    updated_at=excluded.updated_at,
                    runtime_instance_id=excluded.runtime_instance_id,
                    result_json=excluded.result_json,
                    partial_result_json=COALESCE(excluded.partial_result_json, runtime_analysis_projection.partial_result_json)
                """,
                (
                    analysis_id,
                    mission_id,
                    source_url,
                    state,
                    phase,
                    created_at,
                    updated_at,
                    runtime_instance_id,
                    rendered,
                    partial_rendered,
                ),
            )

    def append_event(self, analysis_id: str, event: dict[str, Any]) -> None:
        sequence = int(event["sequence"])
        created_at = str(event["timestamp"])
        rendered = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO runtime_analysis_events(
                    analysis_id, sequence, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (analysis_id, sequence, rendered, created_at),
            )

    def get(self, analysis_id: str) -> AnalysisProjection | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT analysis_id, mission_id, source_url, state, phase,
                       created_at, updated_at, runtime_instance_id, result_json, partial_result_json
                FROM runtime_analysis_projection
                WHERE analysis_id = ?
                """,
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return AnalysisProjection(
            analysis_id=row["analysis_id"],
            mission_id=row["mission_id"],
            source_url=row["source_url"],
            state=row["state"],
            phase=row["phase"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            runtime_instance_id=row["runtime_instance_id"],
            result=result,
            partial_result=json.loads(row["partial_result_json"]) if row["partial_result_json"] else None,
        )

    def events(self, analysis_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT payload_json
                FROM runtime_analysis_events
                WHERE analysis_id = ?
                ORDER BY sequence ASC
                """,
                (analysis_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
