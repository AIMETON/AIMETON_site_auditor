from __future__ import annotations

from app.research_execution import active_settings, run_controlled, ResearchInterrupted, check_execution

import asyncio
import os
from datetime import UTC, datetime
from functools import lru_cache
from threading import RLock
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.analysis_runtime_projection import AnalysisProjection, AnalysisRuntimeProjectionStore
from app.async_enriched_analysis import run_enriched_site_analysis
from app.heuristics import heuristic_analysis
from app.mission_orchestrator import (
    EntryPoint,
    default_site_mission_request,
    get_mission_orchestrator,
    record_legacy_site_turn,
)
from app.models import AnalyzeRequest, ChatRequest
from app.research_control import CONTROLS, authorize_research, bind_research, deep_research_enabled, bind_settings_snapshot, bind_analysis_control, recovered_research_snapshot
from app.public_llm_status import (
    project_public_llm_input_metrics,
    project_public_llm_outcome,
)
from app.runtime_convergence import runtime_instance_id
from app.runtime_time import runtime_time_snapshot
from app.scraper import FetchError, fetch_site
from app.site_applicability import (
    attach_applicability,
    bind_site_applicability,
    classify_site_applicability,
    not_applicable_site_analysis,
    should_short_circuit,
)
from app.trace_context import bind_trace_identity
from app.trace_ledger import SQLiteTraceLedger
from app.umel import get_umel_event


router = APIRouter(prefix="/api/analyze", tags=["analysis-runtime"])
_LOCK = RLock()
_ANALYSES: dict[str, dict[str, Any]] = {}
_MCP_TASKS: set[asyncio.Task[None]] = set()

_DEFAULT_ANALYSIS_DEADLINE_SECONDS = 180.0
_DEFAULT_HEARTBEAT_SECONDS = 15.0
_TERMINAL_ANALYSIS_STATES = frozenset({"completed", "failed"})


class AnalysisNotFoundError(LookupError):
    """Raised when an async analysis identifier is unknown to this runtime."""


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisStartResponse(ApiModel):
    mission_id: str
    analysis_id: str
    state: Literal["queued"]
    status_url: str
    events_url: str


class AnalysisEvent(ApiModel):
    sequence: int
    timestamp: datetime
    phase: str
    event_code: str
    icon: str
    state: Literal[
        "queued",
        "running",
        "degraded",
        "blocked",
        "stalled",
        "completed",
        "failed",
    ]
    icon_key: str
    message: str
    detail: str | None = None
    heartbeat: bool = False
    next_action: str | None = None


def _canonical_now() -> datetime:
    value = runtime_time_snapshot().utc.replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(UTC)


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


def _analysis_deadline_seconds() -> float:
    return _float_env(
        "ANALYSIS_ENRICHMENT_DEADLINE_SECONDS",
        _DEFAULT_ANALYSIS_DEADLINE_SECONDS,
        minimum=30.0,
        maximum=900.0,
    )


def _heartbeat_seconds() -> float:
    return _float_env(
        "ANALYSIS_HEARTBEAT_SECONDS",
        _DEFAULT_HEARTBEAT_SECONDS,
        minimum=5.0,
        maximum=120.0,
    )


def _runtime_db_path() -> str:
    return os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")


def _trace_db_path() -> str:
    return os.getenv(
        "AIMETON_TRACE_DB",
        _runtime_db_path(),
    )


@lru_cache(maxsize=4)
def _trace_ledger_for(path: str) -> SQLiteTraceLedger:
    return SQLiteTraceLedger(path)


@lru_cache(maxsize=4)
def _analysis_projection_for(path: str) -> AnalysisRuntimeProjectionStore:
    return AnalysisRuntimeProjectionStore(path)


def _persist_projection(record: dict[str, Any]) -> None:
    """Persist one observable analysis snapshot without breaking the live path."""
    try:
        _analysis_projection_for(_runtime_db_path()).upsert(
            analysis_id=str(record["analysis_id"]),
            mission_id=str(record["mission_id"]),
            source_url=str(record["source_url"]),
            state=str(record["state"]),
            phase=str(record.get("phase") or "unknown"),
            created_at=str(record["created_at"]),
            updated_at=str(record["updated_at"]),
            runtime_instance_id=str(record["runtime_instance_id"]),
            result=record.get("result"),
            partial_result=record.get("partial_result"),
        )
    except Exception:
        # Durability is additive. A projection failure must not regress the
        # established in-process analysis path.
        return


def _persist_projection_event(analysis_id: str, event: dict[str, Any]) -> None:
    try:
        _analysis_projection_for(_runtime_db_path()).append_event(analysis_id, event)
    except Exception:
        return


def _projection_record(analysis_id: str) -> AnalysisProjection | None:
    try:
        return _analysis_projection_for(_runtime_db_path()).get(analysis_id)
    except Exception:
        return None


def _projection_events(analysis_id: str) -> list[dict[str, Any]]:
    try:
        return _analysis_projection_for(_runtime_db_path()).events(analysis_id)
    except Exception:
        return []


def _trace_runtime_snapshot(mission_id: str, attempt_id: str) -> dict[str, Any]:
    """Return a small fail-open projection of durable search and LLM trace.

    This projection intentionally excludes query text, URLs, prompts and raw
    provider payloads. It is safe to expose through the public read-only MCP
    status tool.
    """
    empty = {
        "queries_planned": 0,
        "queries_finished": 0,
        "provider_calls_finished": 0,
        "provider_failures": 0,
        "active_provider_calls": [],
        "llm_state": None,
        "llm_provider": None,
        "llm_elapsed_seconds": None,
        "llm_budget_seconds": None,
        "llm_overdue": False,
        "llm_input_metrics": None,
        "llm_outcome": None,
    }
    try:
        events = _trace_ledger_for(_trace_db_path()).list_attempt(mission_id, attempt_id)
    except Exception:
        return empty

    planned_queries: set[int] = set()
    finished_queries: set[int] = set()
    provider_calls_finished = 0
    provider_failures = 0
    active: dict[tuple[int, str], Any] = {}
    llm_started = None
    llm_terminal = None

    for event in events:
        query_index_raw = event.metadata.get("query_index")
        try:
            query_index = int(query_index_raw)
        except (TypeError, ValueError):
            query_index = -1

        if event.operation == "query_planned" and query_index >= 0:
            planned_queries.add(query_index)
        elif event.operation == "query_finished" and query_index >= 0:
            finished_queries.add(query_index)
        elif event.operation == "provider_live_started" and event.provider and query_index >= 0:
            active[(query_index, event.provider)] = event
        elif event.operation == "provider_live_finished" and event.provider and query_index >= 0:
            active.pop((query_index, event.provider), None)
            provider_calls_finished += 1
            if event.state.value in {"failed", "blocked"}:
                provider_failures += 1
        elif event.operation == "llm_started":
            llm_started = event
            llm_terminal = None
        elif event.operation in {"llm_finished", "llm_timeout"}:
            llm_terminal = event

    now = _canonical_now()
    active_calls: list[dict[str, Any]] = []
    for (query_index, provider), event in sorted(
        active.items(),
        key=lambda item: item[1].created_at,
    ):
        elapsed_seconds = max(0.0, (now - event.created_at.astimezone(UTC)).total_seconds())
        budget_raw = event.metadata.get("provider_budget_seconds")
        try:
            budget_seconds = max(0.0, float(budget_raw))
        except (TypeError, ValueError):
            budget_seconds = 0.0
        active_calls.append(
            {
                "provider": provider,
                "query_index": query_index,
                "elapsed_seconds": round(elapsed_seconds, 1),
                "provider_budget_seconds": round(budget_seconds, 1) if budget_seconds else None,
                "overdue": bool(budget_seconds and elapsed_seconds > budget_seconds + 2.0),
                "secondary": bool(event.metadata.get("secondary", False)),
            }
        )

    llm_state = None
    llm_provider = None
    llm_elapsed_seconds = None
    llm_budget_seconds = None
    llm_overdue = False
    llm_input_metrics = None
    llm_outcome = None
    if llm_started is not None:
        llm_provider = llm_started.provider or "routerai"
        llm_input_metrics = project_public_llm_input_metrics(llm_started.metadata)
        budget_raw = llm_started.metadata.get("budget_seconds")
        try:
            llm_budget_seconds = max(0.0, float(budget_raw))
        except (TypeError, ValueError):
            llm_budget_seconds = None

        if llm_terminal is None:
            llm_state = "running"
            llm_elapsed_seconds = max(
                0.0,
                (now - llm_started.created_at.astimezone(UTC)).total_seconds(),
            )
            llm_overdue = bool(
                llm_budget_seconds
                and llm_elapsed_seconds > llm_budget_seconds + 2.0
            )
        else:
            llm_state = (
                "timeout"
                if llm_terminal.operation == "llm_timeout"
                else llm_terminal.state.value
            )
            llm_outcome = project_public_llm_outcome(llm_terminal.metadata)
            if llm_terminal.duration_ms is not None:
                llm_elapsed_seconds = llm_terminal.duration_ms / 1000.0
            else:
                llm_elapsed_seconds = max(
                    0.0,
                    (
                        llm_terminal.created_at.astimezone(UTC)
                        - llm_started.created_at.astimezone(UTC)
                    ).total_seconds(),
                )

    return {
        "queries_planned": len(planned_queries),
        "queries_finished": len(finished_queries),
        "provider_calls_finished": provider_calls_finished,
        "provider_failures": provider_failures,
        "active_provider_calls": active_calls[:8],
        "llm_state": llm_state,
        "llm_provider": llm_provider,
        "llm_elapsed_seconds": (
            round(llm_elapsed_seconds, 1)
            if llm_elapsed_seconds is not None
            else None
        ),
        "llm_budget_seconds": (
            round(llm_budget_seconds, 1)
            if llm_budget_seconds is not None
            else None
        ),
        "llm_overdue": llm_overdue,
        "llm_input_metrics": llm_input_metrics,
        "llm_outcome": llm_outcome,
    }


def _interrupted_progress(progress: dict[str, Any]) -> dict[str, Any]:
    """Make old in-flight trace truthful after the owning process has died."""
    projected = dict(progress)
    active = list(projected.get("active_provider_calls") or [])
    projected["interrupted_active_provider_calls"] = len(active)
    projected["active_provider_calls"] = []
    if projected.get("llm_state") == "running":
        projected["llm_state"] = "interrupted"
        projected["llm_overdue"] = False
    return projected


def _restart_interruption_event(
    events: list[dict[str, Any]],
    *,
    previous_state: str,
) -> dict[str, Any]:
    umel = get_umel_event("flow.gap_detected")
    if umel is None:
        raise ValueError("unknown_umel_event:flow.gap_detected")
    return AnalysisEvent(
        sequence=len(events) + 1,
        timestamp=_canonical_now(),
        phase="runtime_restart_detected",
        event_code="flow.gap_detected",
        icon=umel.icon,
        state="stalled",
        icon_key="alert-triangle",
        message="Runtime был перезапущен до завершения анализа.",
        detail=f"Сохранённое состояние до restart: {previous_state}.",
        heartbeat=False,
        next_action="Требуется controlled resume; исходный analysis_id сохранён.",
    ).model_dump(mode="json")


def _append_event(
    analysis_id: str,
    *,
    phase: str,
    event_code: str,
    state: str,
    icon_key: str,
    message: str,
    detail: str | None = None,
    heartbeat: bool = False,
    next_action: str | None = None,
) -> None:
    umel = get_umel_event(event_code)
    if umel is None:
        raise ValueError(f"unknown_umel_event:{event_code}")
    now = _canonical_now()
    event_payload: dict[str, Any]
    record_snapshot: dict[str, Any]
    with _LOCK:
        record = _ANALYSES[analysis_id]
        events = record["events"]
        event_payload = AnalysisEvent(
            sequence=len(events) + 1,
            timestamp=now,
            phase=phase,
            event_code=event_code,
            icon=umel.icon,
            state=state,
            icon_key=icon_key,
            message=message,
            detail=detail,
            heartbeat=heartbeat,
            next_action=next_action,
        ).model_dump(mode="json")
        events.append(event_payload)
        record["state"] = state
        record["phase"] = phase
        record["updated_at"] = now.isoformat()
        record_snapshot = dict(record)
    _persist_projection(record_snapshot)
    _persist_projection_event(analysis_id, event_payload)


def create_analysis_runtime(
    source_url: str,
    *,
    entry_point: EntryPoint,
) -> AnalysisStartResponse:
    """Create the canonical mission + async analysis record without starting work."""
    orchestrator = get_mission_orchestrator()
    mission = orchestrator.create_mission(
        default_site_mission_request(source_url),
        entry_point=entry_point,
    )
    mission_id = mission.contract.mission_id
    analysis_id = mission.contract.analysis_id
    now = _canonical_now().isoformat()
    with _LOCK:
        _ANALYSES[analysis_id] = {
            "analysis_id": analysis_id,
            "mission_id": mission_id,
            "source_url": source_url,
            "runtime_instance_id": runtime_instance_id(),
            "state": "queued",
            "phase": "mission_accepted",
            "created_at": now,
            "updated_at": now,
            "events": [],
            "result": None,
        }
        initial_snapshot = dict(_ANALYSES[analysis_id])
    _persist_projection(initial_snapshot)
    _append_event(
        analysis_id,
        phase="mission_accepted",
        event_code="mission.received",
        state="queued",
        icon_key="inbox",
        message="Задача принята и поставлена в очередь.",
        next_action="Начать подключение к сайту.",
    )
    return AnalysisStartResponse(
        mission_id=mission_id,
        analysis_id=analysis_id,
        state="queued",
        status_url=f"/api/analyze/{analysis_id}",
        events_url=f"/api/analyze/{analysis_id}/events",
    )


def _status_from_projection(projection: AnalysisProjection) -> dict[str, Any]:
    interrupted = (
        projection.state not in _TERMINAL_ANALYSIS_STATES
        and projection.runtime_instance_id != runtime_instance_id()
    )
    payload: dict[str, Any] = {
        "analysis_id": projection.analysis_id,
        "mission_id": projection.mission_id,
        "state": "stalled" if interrupted else projection.state,
        "phase": "runtime_restart_detected" if interrupted else projection.phase,
        "created_at": projection.created_at,
        "updated_at": projection.updated_at,
        "result": projection.result,
        "partial_result": projection.partial_result,
    }
    progress = _trace_runtime_snapshot(projection.mission_id, projection.analysis_id)
    if interrupted:
        payload.update(
            {
                "interrupted_by_runtime_restart": True,
                "interruption_reason": "runtime_instance_changed",
                "previous_state": projection.state,
                "resume_required": True,
                "resume_supported": False,
            }
        )
        progress = _interrupted_progress(progress)
    else:
        payload.update(
            {
                "interrupted_by_runtime_restart": False,
                "resume_required": False,
            }
        )
    payload["progress"] = progress
    research = recovered_research_snapshot(projection.analysis_id)
    if research is not None:
        payload["research"] = research
    return payload


def get_analysis_status_payload(analysis_id: str) -> dict[str, Any]:
    """Return the canonical status projection shared by REST and MCP."""
    with _LOCK:
        record = _ANALYSES.get(analysis_id)
        if record is not None:
            payload = {
                "analysis_id": record["analysis_id"],
                "mission_id": record["mission_id"],
                "state": record["state"],
                "phase": record.get("phase"),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "result": record["result"],
                "partial_result": record.get("partial_result"),
                "interrupted_by_runtime_restart": False,
                "resume_required": False,
            }
        else:
            payload = None
    if payload is None:
        projection = _projection_record(analysis_id)
        if projection is None:
            raise AnalysisNotFoundError("analysis_not_found")
        return _status_from_projection(projection)

    if analysis_id in CONTROLS:
        payload["research"] = CONTROLS[analysis_id].snapshot()
    else:
        research = recovered_research_snapshot(analysis_id)
        if research is not None:
            payload["research"] = research
    with _LOCK:
        if _ANALYSES.get(analysis_id, {}).get("dialogue_reply"):
            payload["dialogue_reply"] = _ANALYSES[analysis_id]["dialogue_reply"]
    payload["progress"] = _trace_runtime_snapshot(
        str(payload["mission_id"]),
        analysis_id,
    )
    return payload


def get_analysis_events_payload(analysis_id: str) -> list[dict[str, Any]]:
    """Return the canonical UMEL event snapshot shared by REST and MCP."""
    with _LOCK:
        record = _ANALYSES.get(analysis_id)
        if record is not None:
            return list(record["events"])

    projection = _projection_record(analysis_id)
    if projection is None:
        raise AnalysisNotFoundError("analysis_not_found")
    events = _projection_events(analysis_id)
    interrupted = (
        projection.state not in _TERMINAL_ANALYSIS_STATES
        and projection.runtime_instance_id != runtime_instance_id()
    )
    if interrupted:
        events.append(
            _restart_interruption_event(
                events,
                previous_state=projection.state,
            )
        )
    return events


def _heartbeat_detail(snapshot: dict[str, Any]) -> str:
    active = snapshot.get("active_provider_calls") or []
    planned = int(snapshot.get("queries_planned") or 0)
    finished = int(snapshot.get("queries_finished") or 0)
    provider_finished = int(snapshot.get("provider_calls_finished") or 0)
    failures = int(snapshot.get("provider_failures") or 0)
    llm_state = snapshot.get("llm_state")
    documents_attempted = int(snapshot.get("documents_attempted") or 0)
    frontier_size = int(snapshot.get("frontier_size") or 0)

    if llm_state == "running":
        elapsed = snapshot.get("llm_elapsed_seconds")
        budget = snapshot.get("llm_budget_seconds")
        elapsed_text = f"{float(elapsed or 0):.1f}s"
        budget_text = f"/{float(budget):.0f}s" if budget else ""
        return (
            f"Поисковые ветви завершены {finished}/{planned or '?'}. "
            f"LLM synthesis: {snapshot.get('llm_last_provider') or snapshot.get('llm_provider') or 'provider'} "
            f"{elapsed_text}{budget_text}."
        )

    if finished >= planned > 0 and frontier_size > 0:
        return (
            f"Поиск завершён {finished}/{planned}. "
            f"Проверка документов: обработано {documents_attempted}, в очереди {frontier_size}."
        )

    if active:
        rendered = ", ".join(
            f"{item['provider']} q#{item['query_index']} {item['elapsed_seconds']}s"
            + (" overdue" if item.get("overdue") else "")
            for item in active[:4]
        )
        return (
            f"Внешнее обогащение: поисковые ветви {finished}/{planned or '?'}, "
            f"provider calls завершено {provider_finished}, ошибок {failures}; "
            f"сейчас активны: {rendered}."
        )

    if llm_state in {"timeout", "failed", "degraded"}:
        return (
            f"Поиск завершён {finished}/{planned or '?'}. "
            f"LLM synthesis завершился состоянием {llm_state}; "
            "готовится резервный результат."
        )

    return (
        f"Внешнее обогащение продолжается: поисковые ветви {finished}/{planned or '?'}, "
        f"provider calls завершено {provider_finished}, ошибок {failures}."
    )


async def _heartbeat_loop(
    *,
    mission_id: str,
    analysis_id: str,
    stop: asyncio.Event,
) -> None:
    interval = _heartbeat_seconds()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            snapshot = _trace_runtime_snapshot(mission_id, analysis_id)
            control = CONTROLS.get(analysis_id)
            if control is not None:
                snapshot = {**snapshot, **control.snapshot()}
            active = snapshot.get("active_provider_calls") or []
            llm_running = snapshot.get("llm_state") == "running"
            overdue = (
                any(bool(item.get("overdue")) for item in active)
                or bool(snapshot.get("llm_overdue"))
            )
            _append_event(
                analysis_id,
                phase=(
                    "llm_synthesis_running"
                    if llm_running
                    else "company_profile_running"
                ),
                event_code="flow.gap_detected" if overdue else "external.waiting",
                state="stalled" if overdue else "running",
                icon_key="alert-triangle" if overdue else "clock",
                message=(
                    "Текущий внешний этап превысил ожидаемый бюджет времени."
                    if overdue
                    else (
                        "LLM-провайдер выполняет аналитический синтез."
                        if llm_running
                        else "Продолжается внешнее обогащение профиля."
                    )
                ),
                detail=_heartbeat_detail(snapshot),
                heartbeat=True,
                next_action=(
                    "Дождаться bounded deadline или перейти на резервный результат."
                    if overdue
                    else (
                        "Дождаться результата LLM или его локального fallback."
                        if llm_running
                        else "Дождаться оставшихся поисковых ветвей и синтеза."
                    )
                ),
            )


async def _run_enriched_bounded(
    *,
    source_url: str,
    title: str,
    text: str,
    analysis_id: str,
    site_applicability=None,
):
    async def invoke():
        if site_applicability is None:
            return await run_enriched_site_analysis(source_url, title, text)
        with bind_site_applicability(site_applicability):
            return await run_enriched_site_analysis(source_url, title, text)

    if deep_research_enabled() or active_settings():
        return await invoke()
    deadline_seconds = _analysis_deadline_seconds()
    try:
        return await asyncio.wait_for(
            invoke(),
            timeout=deadline_seconds,
        )
    except asyncio.TimeoutError:
        _append_event(
            analysis_id,
            phase="company_profile_deadline",
            event_code="service.degraded",
            state="degraded",
            icon_key="alert-triangle",
            message="Внешнее обогащение достигло общего лимита времени.",
            detail=f"Bounded deadline: {deadline_seconds:.0f} s. Используем резервный локальный анализ.",
            next_action="Сформировать частичный результат вместо бесконечного ожидания.",
        )
        result = heuristic_analysis(source_url, title, text)
        if site_applicability is not None:
            result = attach_applicability(result, site_applicability)
        result.readiness.provider_states["external_enrichment"] = "deadline_exceeded"
        result.risks_and_assumptions.append(
            f"External enrichment exceeded the bounded {deadline_seconds:.0f}s runtime deadline; "
            "the mission returned a local fallback instead of waiting indefinitely."
        )
        return result


async def _run_analysis_body(
    *,
    source_url: str,
    mission_id: str,
    analysis_id: str,
) -> None:
    orchestrator = get_mission_orchestrator()
    final_url = source_url
    heartbeat_stop: asyncio.Event | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        _append_event(
            analysis_id,
            phase="site_fetch_started",
            event_code="provider.requested",
            state="running",
            icon_key="globe",
            message="Подключаемся к сайту.",
            heartbeat=True,
            next_action="Получить доступный текст и метаданные сайта.",
        )
        page = await fetch_site(source_url)
        final_url = page["final_url"]
        applicability = await classify_site_applicability(
            final_url,
            page["title"],
            page["text"],
        )
        if should_short_circuit(applicability):
            result = not_applicable_site_analysis(
                final_url,
                page["title"],
                applicability,
            ).model_copy(update={"mission_id": mission_id, "analysis_id": analysis_id})
            record_legacy_site_turn(
                orchestrator,
                mission_id,
                final_url=final_url,
                succeeded=True,
            )
            with _LOCK:
                _ANALYSES[analysis_id]["result"] = result.model_dump(mode="json")
            _append_event(
                analysis_id,
                phase="completed",
                event_code="mission.completed",
                state="completed",
                icon_key="check-circle",
                message="Вход классифицирован как не относящийся к сайту компании.",
                detail=applicability.reason,
                next_action="Укажите официальный сайт конкретной коммерческой организации.",
            )
            return
        # Persist a local, explicitly preliminary report only after applicability gate.
        partial = attach_applicability(
            heuristic_analysis(final_url, page["title"], page["text"]),
            applicability,
        )
        partial.mission_id, partial.analysis_id = mission_id, analysis_id
        partial.research_status.update(result_quality="partial", checkpoint_stage="site_acquired")
        partial.readiness.provider_states["external_enrichment"] = "not_completed"
        partial.readiness.release_blockers.append("audit_not_completed")
        partial.risks_and_assumptions.insert(0,
            "Частичный предварительный отчёт по странице сайта. Внешнее исследование и проверка выводов не завершены.")
        with _LOCK:
            _ANALYSES[analysis_id]["partial_result"] = partial.model_dump(mode="json")
            partial_snapshot = dict(_ANALYSES[analysis_id])
        _persist_projection(partial_snapshot)
        check_execution()
        control = CONTROLS.get(analysis_id)
        if control and control.stop_requested:
            raise ResearchInterrupted(control.stop_reason or "stopped_by_user")
        _append_event(
            analysis_id,
            phase="site_fetch_completed",
            event_code="data.received",
            state="running",
            icon_key="check",
            message="Сайт получен. Восстанавливаем профиль компании.",
            next_action="Собрать профиль компании и внешние свидетельства.",
        )
        _append_event(
            analysis_id,
            phase="company_profile_started",
            event_code="picture.assembled",
            state="running",
            icon_key="building",
            message="Восстанавливаем профиль компании и коммерческий контекст.",
            heartbeat=True,
            next_action="Синтезировать evidence и AI-возможности.",
        )
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                mission_id=mission_id,
                analysis_id=analysis_id,
                stop=heartbeat_stop,
            ),
            name=f"aimeton-analysis-heartbeat:{analysis_id}",
        )
        with bind_trace_identity(mission_id, analysis_id), bind_research(CONTROLS.get(analysis_id)):
            result = await _run_enriched_bounded(
                source_url=page["final_url"],
                title=page["title"],
                text=page["text"],
                analysis_id=analysis_id,
                site_applicability=applicability,
            )
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_task is not None:
            await heartbeat_task
            heartbeat_task = None

        _append_event(
            analysis_id,
            phase="company_profile_completed",
            event_code="stage.completed",
            state="running",
            icon_key="check-circle",
            message="Обогащение и аналитический синтез завершены.",
            detail=_heartbeat_detail(_trace_runtime_snapshot(mission_id, analysis_id)),
            next_action="Зафиксировать итоговый результат миссии.",
        )
        # Once enriched analysis has returned, no paid/provider work remains.
        # A stop racing this final commit must not discard an already-computed dossier.
        control = CONTROLS.get(analysis_id)
        late_stop_requested = bool(control and control.stop_requested)
        if late_stop_requested:
            result.research_status["completion_after_stop_requested"] = True
        record_legacy_site_turn(
            orchestrator,
            mission_id,
            final_url=page["final_url"],
            succeeded=True,
        )
        with _LOCK:
            _ANALYSES[analysis_id]["result"] = result.model_copy(
                update={"mission_id": mission_id, "analysis_id": analysis_id}
            ).model_dump(mode="json")
        _append_event(
            analysis_id,
            phase="completed",
            event_code="mission.completed",
            state="completed",
            icon_key="check-circle",
            message=(
                "Остановка запрошена после завершения аналитики; готовый результат сохранён."
                if late_stop_requested
                else "Анализ завершён. Результат готов."
            ),
        )
    except (FetchError, httpx.HTTPError, ValueError):
        record_legacy_site_turn(
            orchestrator,
            mission_id,
            final_url=final_url,
            succeeded=False,
        )
        _append_event(
            analysis_id,
            phase="failed",
            event_code="mission.failed",
            state="failed",
            icon_key="alert-triangle",
            message="Анализ не завершён.",
            detail="Проверьте адрес сайта и повторите запуск.",
            next_action="Повторить анализ после проверки доступности сайта.",
        )
    except Exception as exc:
        record_legacy_site_turn(
            orchestrator,
            mission_id,
            final_url=final_url,
            succeeded=False,
        )
        _append_event(
            analysis_id,
            phase="failed",
            event_code="mission.failed",
            state="failed",
            icon_key="alert-triangle",
            message="Анализ остановлен внутренней ошибкой.",
            detail=f"Санитизированный тип ошибки: {type(exc).__name__}.",
            next_action="Проверить trace по mission_id и повторить после устранения причины.",
        )
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


async def _run_analysis(*, source_url: str, mission_id: str, analysis_id: str) -> None:
    try:
        await run_controlled(CONTROLS.get(analysis_id), lambda: _run_analysis_body(
            source_url=source_url, mission_id=mission_id, analysis_id=analysis_id))
    except ResearchInterrupted as exc:
        # run_controlled performs a final stop check after the operation returns.
        # If the body already committed a complete result, that late stop is a no-op:
        # never rewrite a terminal completed dossier as failed.
        with _LOCK:
            committed = _ANALYSES.get(analysis_id)
            completed_result_committed = bool(
                committed
                and committed.get("state") == "completed"
                and committed.get("result") is not None
            )
        if completed_result_committed:
            return
        record_legacy_site_turn(get_mission_orchestrator(), mission_id, final_url=source_url, succeeded=False)
        _append_event(analysis_id, phase=exc.reason, event_code="mission.failed", state="failed",
                      icon_key="clock", message="Исследование остановлено по настройкам пользователя.",
                      detail=exc.reason, next_action="Исследование не завершено; доступный частичный отчёт сохранён отдельно.")


def schedule_analysis_runtime(
    *,
    source_url: str,
    mission_id: str,
    analysis_id: str,
) -> None:
    """Schedule one MCP-started analysis and keep a strong task reference."""
    task = asyncio.create_task(
        _run_analysis(
            source_url=source_url,
            mission_id=mission_id,
            analysis_id=analysis_id,
        ),
        name=f"aimeton-analysis:{analysis_id}",
    )
    _MCP_TASKS.add(task)
    task.add_done_callback(_MCP_TASKS.discard)


@router.post(
    "/start",
    response_model=AnalysisStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks, request: Request = None):
    control = authorize_research(req, request)
    started = create_analysis_runtime(
        str(req.url),
        entry_point=EntryPoint.LEGACY_ADAPTER,
    )
    try:
        bind_settings_snapshot(control, started.mission_id, started.analysis_id)
    except HTTPException:
        _append_event(started.analysis_id, phase="settings_conflict", event_code="mission.failed", state="failed",
                      icon_key="alert-triangle", message="Настройки изменились; повторите запуск.")
        raise
    bind_analysis_control(control, started.analysis_id)
    background_tasks.add_task(
        _run_analysis,
        source_url=str(req.url),
        mission_id=started.mission_id,
        analysis_id=started.analysis_id,
    )
    return started


@router.get("/{analysis_id}")
def get_analysis_status(analysis_id: str):
    try:
        return get_analysis_status_payload(analysis_id)
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="analysis_not_found") from exc


@router.get("/{analysis_id}/events", response_model=list[AnalysisEvent])
def get_analysis_events(analysis_id: str):
    try:
        return get_analysis_events_payload(analysis_id)
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="analysis_not_found") from exc


@router.post("/{analysis_id}/stop")
async def stop_deep_research(analysis_id: str, request: Request):
    from app.auth_api import get_auth_provider, _resolve_session, _auth_error, _require_csrf
    resolution = _resolve_session(get_auth_provider(), request.cookies.get("aimeton_session", ""))
    if resolution.failure is not None:
        raise _auth_error(resolution.failure)
    _require_csrf(request.cookies.get("aimeton_csrf"), request.headers.get("X-CSRF-Token"))
    control = CONTROLS.get(analysis_id)
    if control is None or control.owner_id != resolution.user.id:
        raise HTTPException(status_code=404, detail="research_not_found")
    with _LOCK:
        terminal_state = _ANALYSES.get(analysis_id, {}).get("state")
    if terminal_state in _TERMINAL_ANALYSIS_STATES:
        return {"state": terminal_state, "research": control.snapshot()}
    control.stop_requested = True
    control.stop_reason = "stopped_by_user"
    control.checkpoint("stop", control.snapshot())
    _append_event(analysis_id, phase="stop_requested", event_code="service.degraded", state="running",
                  icon_key="clock", message="Останавливаем исследование и сохраняем полученные данные.",
                  next_action="Дождаться текущих запросов; новые запросы не запускаются.")
    return {"state": "stop_requested", "research": control.snapshot()}


async def _run_dialogue_research(req: ChatRequest, started: AnalysisStartResponse):
    from app.audit_dialogue import run_audit_dialogue
    analysis_id = started.analysis_id
    _append_event(analysis_id, phase="dialogue_research", event_code="mission.received", state="running",
                  icon_key="search", message="Углубляем профиль по вашему уточнению.")
    try:
        with bind_research(CONTROLS.get(analysis_id)), bind_trace_identity(started.mission_id, analysis_id):
            response = await run_audit_dialogue(req)
        with _LOCK:
            _ANALYSES[analysis_id]["result"] = response["analysis"].model_dump(mode="json")
            _ANALYSES[analysis_id]["dialogue_reply"] = response["reply"]
        _append_event(analysis_id, phase="completed", event_code="mission.completed", state="completed",
                      icon_key="check-circle", message="Уточнённый профиль сохранён.")
    except Exception as exc:
        _append_event(analysis_id, phase="failed", event_code="mission.failed", state="failed",
                      icon_key="alert-triangle", message="Исследование прервано.", detail=type(exc).__name__)


@router.post("/refine/start", response_model=AnalysisStartResponse, status_code=202)
async def start_deep_refinement(req: ChatRequest, background_tasks: BackgroundTasks, request: Request = None):
    if req.research_settings_revision is not None:
        raise HTTPException(status_code=409, detail="settings_not_supported_for_refinement")
    if not req.deep_research or not req.refine_search:
        raise HTTPException(status_code=422, detail="deep_refinement_requires_search_and_budget_consent")
    control = authorize_research(req, request, use_saved_settings=False)
    started = create_analysis_runtime(req.analysis.url, entry_point=EntryPoint.LEGACY_ADAPTER)
    bind_analysis_control(control, started.analysis_id)
    background_tasks.add_task(_run_dialogue_research, req, started)
    return started
