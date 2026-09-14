from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.trace_ledger import SQLiteTraceLedger, TraceEvent, sanitize_metadata


_QUERY_PLAN_OPERATIONS = {
    "hunt_plan",
    "hunt_search_policy_resolved",
    "hunt_search_continuation_steering",
    "hunt_search_wave_observed",
    "hunt_search_wave_shadow_observer",
}
_RAW_INTAKE_OPERATIONS = {
    "candidate_excluded",
    "candidate_deduplicated",
    "candidate_pool_omitted",
    "candidate_dedupe_retained",
}
_QUALIFICATION_OPERATIONS = {
    "candidate_pre_scored",
    "candidate_rejected",
    "candidate_observation",
    "candidate_returned",
    "candidate_output_omitted",
}
_DEEP_AUDIT_OPERATIONS = {
    "candidate_deep_audit_started",
    "candidate_deep_audit_completed",
    "candidate_deep_audit_failed",
    "candidate_processing_failed",
}


def trace_db_path() -> Path:
    return Path(
        os.getenv(
            "AIMETON_TRACE_DB",
            os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"),
        )
    )


def _ledger() -> SQLiteTraceLedger:
    return SQLiteTraceLedger(trace_db_path())


def _event_projection(event: TraceEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "created_at": event.created_at.isoformat(),
        "component": event.component,
        "operation": event.operation,
        "state": event.state.value,
        "reason_code": event.reason_code,
        "summary": event.summary,
        "provider": event.provider,
        "duration_ms": event.duration_ms,
        "counters": dict(event.counters),
        "metadata": sanitize_metadata(dict(event.metadata)),
    }


def _is_provider_event(event: TraceEvent) -> bool:
    component = event.component.casefold()
    operation = event.operation.casefold()
    return bool(
        event.provider
        or "search_gateway" in component
        or "provider" in component
        or operation.startswith("search_")
        or "provider" in operation
        or operation in {
            "hunt_search_policy_resolved",
            "hunt_search_continuation_steering",
            "hunt_search_wave_observed",
            "hunt_search_wave_shadow_observer",
            "hunt_search_query_failed",
        }
    )


def _plan_scope(events: list[TraceEvent]) -> dict[str, Any]:
    plan = next(
        (
            event
            for event in events
            if event.component == "hunter" and event.operation == "hunt_plan"
        ),
        None,
    )
    if plan is None:
        return {}
    metadata = sanitize_metadata(dict(plan.metadata))
    return {
        "plan_source": metadata.get("plan_source"),
        "input_region": metadata.get("input_region"),
        "effective_region": metadata.get("effective_region"),
        "input_industries": metadata.get("input_industries"),
        "effective_industries": metadata.get("effective_industries"),
        "input_focus": metadata.get("input_focus"),
        "effective_focus": metadata.get("effective_focus"),
        "queries": metadata.get("queries"),
        "minimum_pre_score": metadata.get("minimum_pre_score"),
        "deep_audit_score": metadata.get("deep_audit_score"),
        "concurrency": metadata.get("concurrency"),
    }


def list_recent_hunter_attempts(limit: int = 20) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    ledger = _ledger()
    with ledger._connect() as db:
        rows = db.execute(
            """
            SELECT
                mission_id,
                attempt_id,
                MIN(created_at) AS started_at,
                MAX(created_at) AS updated_at,
                COUNT(*) AS hunter_event_count
            FROM mission_trace_events
            WHERE component = 'hunter'
            GROUP BY mission_id, attempt_id
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        events = ledger.list_attempt(str(row["mission_id"]), str(row["attempt_id"]))
        hunter_events = [event for event in events if event.component == "hunter"]
        if not hunter_events:
            continue
        last = hunter_events[-1]
        scope = _plan_scope(events)
        result.append(
            {
                "mission_id": str(row["mission_id"]),
                "attempt_id": str(row["attempt_id"]),
                "started_at": str(row["started_at"]),
                "updated_at": str(row["updated_at"]),
                "hunter_event_count": int(row["hunter_event_count"]),
                "last_operation": last.operation,
                "last_state": last.state.value,
                "complete": last.operation == "hunt_funnel_complete",
                "scope": scope,
            }
        )
    return result


def build_hunter_diagnostic_snapshot(mission_id: str, attempt_id: str) -> dict[str, Any] | None:
    events = _ledger().list_attempt(mission_id, attempt_id)
    hunter_events = [event for event in events if event.component == "hunter"]
    if not hunter_events:
        return None

    projected = [_event_projection(event) for event in events]
    query_plan = [
        _event_projection(event)
        for event in events
        if event.operation in _QUERY_PLAN_OPERATIONS
    ]
    providers = [_event_projection(event) for event in events if _is_provider_event(event)]
    raw_intake = [
        _event_projection(event)
        for event in events
        if event.operation in _RAW_INTAKE_OPERATIONS
    ]
    qualification = [
        _event_projection(event)
        for event in events
        if event.operation in _QUALIFICATION_OPERATIONS
    ]
    deep_audit = [
        _event_projection(event)
        for event in events
        if event.operation in _DEEP_AUDIT_OPERATIONS
    ]

    return {
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "scope": _plan_scope(events),
        "complete": hunter_events[-1].operation == "hunt_funnel_complete",
        "last_operation": hunter_events[-1].operation,
        "last_state": hunter_events[-1].state.value,
        "sections": {
            "query_plan": query_plan,
            "providers": providers,
            "raw_intake": raw_intake,
            "qualification": qualification,
            "deep_audit": deep_audit,
            "funnel_trace": projected,
        },
        "limitations": [
            "Диагностика показывает санитизированные trace-события, а не raw provider payload.",
            "Секреты, Authorization/cookie/token/API-key и prompt-поля редактируются до сохранения и повторно при чтении.",
            "Raw / intake отражает переходы raw→dedupe→candidate; полный текст каждого сырого ответа провайдера намеренно не хранится.",
        ],
    }
