from __future__ import annotations

import hashlib
import json
from typing import Any

from app.mission_sqlite import SQLiteMissionRepository
from app.trace_context import current_trace_identity


COMPILED_CONTEXT_SCHEMA_VERSION = 1


class CompiledContextPersistenceError(RuntimeError):
    pass


def persist_compiled_context(
    context_json: str,
    stats: dict[str, Any],
    *,
    repository: SQLiteMissionRepository | None = None,
) -> str | None:
    """Persist the deterministic pre-LLM company context for mission recovery."""
    identity = current_trace_identity()
    if identity is None:
        return None

    repo = repository or SQLiteMissionRepository()
    mission = repo.get_for_admin(identity.mission_id)
    if mission is None:
        return None

    record_id = f"compiled_context_{identity.attempt_id}"
    existing = repo.records_for_owner(mission.owner_id, mission.id) or []
    for record in existing:
        if record.get("id") == record_id:
            if record.get("kind") != "compiled_context":
                raise CompiledContextPersistenceError("compiled_context_record_id_collision")
            return record_id

    payload = {
        "schema_version": COMPILED_CONTEXT_SCHEMA_VERSION,
        "context": json.loads(context_json),
        "stats": dict(stats),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    try:
        return repo.append_record(
            mission.id,
            "compiled_context",
            payload,
            digest=digest,
            record_id=record_id,
        )
    except Exception as exc:
        raise CompiledContextPersistenceError("compiled_context_persist_failed") from exc
