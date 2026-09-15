from __future__ import annotations

from app.research_execution import active_settings

import asyncio
import json
import os
import time
from functools import lru_cache
from typing import Any

import httpx

from app.llm import MODEL, analyze_with_routerai
from app.models import SiteAnalysis
from app.research_control import deep_research_enabled
from app.routerai_evidence_units import DEFAULT_EVIDENCE_CHUNK_CHARS, EvidenceCoverageOverflow, chunk_text
from app.routerai_projection_metrics import routerai_projection_metrics
from app.routerai_split_synthesis import (
    SplitSynthesisPhaseError,
    SplitSynthesisPhaseTimeout,
)
from app.routerai_split_v2 import analyze_with_routerai_split_v2
from app.trace_context import current_trace_identity
from app.trace_ledger import TraceEventCreate, TraceState
from app.trace_write_metrics import InstrumentedSQLiteTraceLedger


_DEFAULT_ROUTERAI_ANALYSIS_TIMEOUT_SECONDS = 60.0


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def routerai_analysis_timeout_seconds() -> float:
    """Bound one analytical LLM turn inside the wider mission deadline."""
    return _float_env(
        "ROUTERAI_ANALYSIS_TIMEOUT_SECONDS",
        _DEFAULT_ROUTERAI_ANALYSIS_TIMEOUT_SECONDS,
        minimum=10.0,
        maximum=120.0,
    )


def routerai_split_synthesis_enabled() -> bool:
    """Default async synthesis to split mode while preserving one-switch rollback."""
    value = os.getenv("ROUTERAI_SPLIT_SYNTHESIS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def routerai_input_metrics(
    text: str,
    external_sources: list[dict] | None,
) -> dict[str, Any]:
    """Return truthful aggregate input-size metrics without retaining content."""
    sources = external_sources or []
    serialized_sources = json.dumps(
        sources,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    official_text_chars = len(text)
    external_context_chars = len(serialized_sources)
    schema_chars = len(
        json.dumps(SiteAnalysis.model_json_schema(), ensure_ascii=False)
    )
    estimated_total_input_chars = (
        official_text_chars + external_context_chars + schema_chars
    )
    official_chunks_estimated = len(chunk_text(text))
    external_chunks_estimated = (
        0
        if not serialized_sources or not sources
        else max(
            1,
            (external_context_chars + DEFAULT_EVIDENCE_CHUNK_CHARS - 1)
            // DEFAULT_EVIDENCE_CHUNK_CHARS,
        )
    )
    metrics: dict[str, Any] = {
        "model": MODEL[:160],
        "official_text_chars": official_text_chars,
        "external_context_chars": external_context_chars,
        "external_source_count": len(sources),
        "schema_chars": schema_chars,
        "estimated_total_input_chars": estimated_total_input_chars,
        "dynamic_input_chars": estimated_total_input_chars,
        "official_chunks_estimated": official_chunks_estimated,
        "external_chunks_estimated": external_chunks_estimated,
        "input_truncated": False,
    }
    metrics.update(routerai_projection_metrics(sources))
    return metrics


def _trace_db_path() -> str:
    return os.getenv(
        "AIMETON_TRACE_DB",
        os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"),
    )


@lru_cache(maxsize=4)
def _trace_ledger_for(path: str) -> InstrumentedSQLiteTraceLedger:
    return InstrumentedSQLiteTraceLedger(path)


def _trace(
    *,
    operation: str,
    state: TraceState,
    reason_code: str,
    summary: str,
    duration_ms: int | None = None,
    budget_seconds: float,
    error_type: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Append a safe LLM span event when a mission trace identity is bound."""
    identity = current_trace_identity()
    if identity is None:
        return
    metadata: dict[str, Any] = {
        "budget_seconds": round(budget_seconds, 3),
        "model_family": "routerai",
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    if error_type:
        metadata["error_type"] = error_type[:128]
    try:
        _trace_ledger_for(_trace_db_path()).append(
            TraceEventCreate(
                mission_id=identity.mission_id,
                attempt_id=identity.attempt_id,
                component="llm_synthesis",
                operation=operation,
                state=state,
                reason_code=reason_code,
                summary=summary,
                provider="routerai",
                duration_ms=duration_ms,
                metadata=metadata,
                event_key=(
                    f"{identity.mission_id}:{identity.attempt_id}:"
                    f"routerai-analysis:{operation}"
                ),
                runtime_version=os.getenv("AIMETON_RUNTIME_VERSION") or None,
            )
        )
    except Exception:
        # Observability is fail-open and must never break the analytical path.
        return


async def run_bounded_routerai_analysis(
    url: str,
    title: str,
    text: str,
    external_sources: list[dict] | None = None,
) -> SiteAnalysis:
    """Run RouterAI analysis with a hard wall-clock deadline and rollbackable split mode."""
    budget_seconds = 0.0 if active_settings() else routerai_analysis_timeout_seconds()
    deep = deep_research_enabled()
    use_split = deep or routerai_split_synthesis_enabled()
    input_metrics = routerai_input_metrics(text, external_sources)
    input_metrics["unlimited_llm_budget"] = deep
    input_metrics["synthesis_mode"] = "split_v2_parallel" if use_split else "legacy_monolith"
    started = time.perf_counter()
    _trace(
        operation="llm_started",
        state=TraceState.STARTED,
        reason_code="llm_request_inflight",
        summary="RouterAI analytical synthesis started",
        budget_seconds=budget_seconds,
        extra_metadata=input_metrics,
    )
    analysis_fn = analyze_with_routerai_split_v2 if use_split else analyze_with_routerai
    try:
        if not use_split and (
            len(text) > 30000
            or len(json.dumps(external_sources or [], ensure_ascii=False, indent=2)) > 52000
        ):
            raise EvidenceCoverageOverflow("legacy_monolith_cannot_cover_input")
        result = await asyncio.wait_for(
            analysis_fn(url, title, text, external_sources),
            timeout=None if deep or active_settings() else budget_seconds,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException, SplitSynthesisPhaseTimeout) as exc:
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        error_metadata = dict(input_metrics)
        error_metadata["outcome"] = "timeout"
        phase = getattr(exc, "phase", None)
        if phase:
            error_metadata["failed_phase"] = str(phase)[:128]
        _trace(
            operation="llm_timeout",
            state=TraceState.DEGRADED,
            reason_code="llm_deadline_exceeded",
            summary="RouterAI analytical synthesis exceeded its bounded deadline",
            duration_ms=duration_ms,
            budget_seconds=budget_seconds,
            error_type=type(exc).__name__,
            extra_metadata=error_metadata,
        )
        raise TimeoutError("routerai_analysis_deadline_exceeded") from exc
    except Exception as exc:
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        error_metadata = dict(input_metrics)
        error_metadata["outcome"] = "failed"
        phase = getattr(exc, "phase", None)
        if phase:
            error_metadata["failed_phase"] = str(phase)[:128]
        if isinstance(exc, SplitSynthesisPhaseError):
            error_metadata["phase_error_type"] = exc.error_type[:128]
        _trace(
            operation="llm_finished",
            state=TraceState.FAILED,
            reason_code="llm_request_failed",
            summary="RouterAI analytical synthesis failed",
            duration_ms=duration_ms,
            budget_seconds=budget_seconds,
            error_type=type(exc).__name__,
            extra_metadata=error_metadata,
        )
        raise

    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    success_metadata = dict(input_metrics)
    success_metadata["outcome"] = "succeeded"
    _trace(
        operation="llm_finished",
        state=TraceState.SUCCEEDED,
        reason_code="llm_request_succeeded",
        summary="RouterAI analytical synthesis completed",
        duration_ms=duration_ms,
        budget_seconds=budget_seconds,
        extra_metadata=success_metadata,
    )
    return result
