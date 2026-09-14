from __future__ import annotations

from pathlib import Path

from app.auth_api import require_admin
from app.hunter_diagnostics import (
    build_hunter_diagnostic_snapshot,
    list_recent_hunter_attempts,
)
from app.runtime_core import api as runtime_api
from app.trace_ledger import RetentionClass, SQLiteTraceLedger, TraceEventCreate, TraceState


def _append(
    ledger: SQLiteTraceLedger,
    *,
    sequence: int,
    operation: str,
    component: str = "hunter",
    state: TraceState = TraceState.SUCCEEDED,
    metadata: dict | None = None,
    counters: dict[str, int] | None = None,
    provider: str | None = None,
) -> None:
    ledger.append(
        TraceEventCreate(
            mission_id="hunt-debug",
            attempt_id="corr-debug",
            component=component,
            operation=operation,
            state=state,
            reason_code=f"reason-{operation}",
            summary=f"summary {operation}",
            provider=provider,
            counters=counters or {},
            metadata=metadata or {},
            event_key=f"hunt-debug:corr-debug:{sequence}:{component}:{operation}",
            retention_class=RetentionClass.FORENSIC,
        )
    )


def test_hunter_diagnostic_snapshot_projects_sections_and_redacts(tmp_path: Path, monkeypatch) -> None:
    trace_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(trace_path))
    monkeypatch.delenv("AIMETON_TRACE_DB", raising=False)
    ledger = SQLiteTraceLedger(trace_path)

    _append(
        ledger,
        sequence=1,
        operation="hunt_plan",
        metadata={
            "plan_source": "llm",
            "effective_region": "Красноярск",
            "effective_industries": ["стоматология"],
            "queries": ["стоматология Красноярск официальный сайт"],
            "minimum_pre_score": 35,
            "deep_audit_score": 60,
            "api_key": "must-not-leak",
        },
    )
    _append(
        ledger,
        sequence=2,
        component="search_gateway",
        operation="search_provider_attempt",
        provider="fake",
        metadata={"latency_ms": 12, "authorization": "must-not-leak"},
    )
    _append(
        ledger,
        sequence=3,
        operation="candidate_dedupe_retained",
        metadata={"candidate_url": "https://clinic.ru/"},
    )
    _append(
        ledger,
        sequence=4,
        operation="candidate_pre_scored",
        metadata={"pre_score": 90, "factor_region_match": 25, "factor_industry_match": 25},
    )
    _append(
        ledger,
        sequence=5,
        operation="candidate_deep_audit_completed",
        metadata={"region_confirmed": True},
    )
    _append(
        ledger,
        sequence=6,
        operation="hunt_funnel_complete",
        counters={"qualified_candidates": 1, "returned_candidates": 1},
    )

    snapshot = build_hunter_diagnostic_snapshot("hunt-debug", "corr-debug")
    assert snapshot is not None
    assert snapshot["complete"] is True
    assert snapshot["scope"]["effective_region"] == "Красноярск"
    assert snapshot["scope"]["effective_industries"] == ["стоматология"]
    assert snapshot["sections"]["query_plan"]
    assert snapshot["sections"]["providers"]
    assert snapshot["sections"]["raw_intake"]
    assert snapshot["sections"]["qualification"]
    assert snapshot["sections"]["deep_audit"]
    assert snapshot["sections"]["funnel_trace"][-1]["operation"] == "hunt_funnel_complete"
    assert snapshot["sections"]["query_plan"][0]["metadata"]["api_key"] == "[REDACTED]"
    provider_event = next(
        event for event in snapshot["sections"]["providers"]
        if event["operation"] == "search_provider_attempt"
    )
    assert provider_event["metadata"]["authorization"] == "[REDACTED]"


def test_recent_hunter_attempts_include_scope_and_completion(tmp_path: Path, monkeypatch) -> None:
    trace_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(trace_path))
    monkeypatch.delenv("AIMETON_TRACE_DB", raising=False)
    ledger = SQLiteTraceLedger(trace_path)
    _append(
        ledger,
        sequence=1,
        operation="hunt_plan",
        metadata={
            "plan_source": "deterministic_fallback",
            "effective_region": "Красноярск",
            "effective_industries": ["стоматология"],
            "queries": ["стоматология Красноярск"],
        },
    )
    _append(ledger, sequence=2, operation="candidate_pre_scored")

    attempts = list_recent_hunter_attempts(10)
    assert len(attempts) == 1
    assert attempts[0]["mission_id"] == "hunt-debug"
    assert attempts[0]["attempt_id"] == "corr-debug"
    assert attempts[0]["complete"] is False
    assert attempts[0]["scope"]["effective_region"] == "Красноярск"


def test_hunter_diagnostic_routes_require_admin_dependency() -> None:
    diagnostic_routes = [
        route
        for route in runtime_api.router.routes
        if getattr(route, "path", "").startswith("/api/runtime/hunter-diagnostics")
    ]
    assert len(diagnostic_routes) == 2
    for route in diagnostic_routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert require_admin in calls
