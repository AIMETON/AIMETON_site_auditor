from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent_activity_api import router as activity_router
from app.auth_api import require_admin
from app.hunter_diagnostics import (
    build_hunter_diagnostic_snapshot,
    list_recent_hunter_attempts,
)
from app.runtime_convergence import runtime_convergence_snapshot
from app.runtime_core.models import (
    Event,
    EventCreate,
    Evidence,
    EvidenceCreate,
    PlanStep,
    PlanStepCreate,
    Task,
    TaskCreate,
    TaskTransition,
    ToolExecution,
    ToolExecutionCreate,
)
from app.runtime_core.storage import RuntimeStore
from app.runtime_time import (
    RuntimeTimeHealth,
    RuntimeTimeSnapshot,
    runtime_time_health,
    runtime_time_snapshot,
)
from app.umel import UMEL_VERSION, UmelEvent, list_umel_events

router = APIRouter(prefix="/api/runtime", tags=["runtime-core"])
router.include_router(activity_router)
store = RuntimeStore()


@router.get("/health")
def runtime_health() -> dict[str, object]:
    return {"status": "ok", "component": "aimeton-runtime-core", "schema_version": 1}


@router.get("/time", response_model=RuntimeTimeSnapshot)
def runtime_time() -> RuntimeTimeSnapshot:
    return runtime_time_snapshot()


@router.get("/time/health", response_model=RuntimeTimeHealth)
def runtime_time_status() -> RuntimeTimeHealth:
    return runtime_time_health()


@router.get("/convergence")
def runtime_convergence() -> dict[str, object]:
    """Return restart-aware Stage convergence without exposing secrets."""
    return runtime_convergence_snapshot()


@router.get("/umel")
def runtime_umel_registry() -> dict[str, object]:
    return {
        "version": UMEL_VERSION,
        "events": [event.model_dump(mode="json") for event in list_umel_events()],
    }


@router.get("/hunter-diagnostics/attempts")
def hunter_diagnostic_attempts(
    limit: int = Query(default=20, ge=1, le=100),
    _admin=Depends(require_admin),
) -> dict[str, object]:
    """List recent Hunter attempts for the privileged developer diagnostic surface."""
    return {
        "attempts": list_recent_hunter_attempts(limit),
        "sanitized": True,
        "admin_only": True,
    }


@router.get("/hunter-diagnostics/{mission_id}/{attempt_id}")
def hunter_diagnostic_snapshot(
    mission_id: str,
    attempt_id: str,
    _admin=Depends(require_admin),
) -> dict[str, object]:
    """Project one Hunter forensic trace into bounded diagnostic tabs."""
    snapshot = build_hunter_diagnostic_snapshot(mission_id, attempt_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Hunter diagnostic attempt not found")
    return snapshot


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(request: TaskCreate) -> Task:
    return store.create_task(request)


@router.get("/tasks", response_model=list[Task])
def list_tasks(limit: int = Query(default=100, ge=1, le=1000)) -> list[Task]:
    return store.list_tasks(limit)


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str) -> Task:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Runtime task not found")
    return task


@router.post("/tasks/{task_id}/transition", response_model=Task)
def transition_task(task_id: str, request: TaskTransition) -> Task:
    try:
        return store.transition_task(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc


@router.post("/tasks/{task_id}/events", response_model=Event, status_code=201)
def append_event(task_id: str, request: EventCreate) -> Event:
    try:
        return store.append_event(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc


@router.post("/tasks/{task_id}/evidence", response_model=Evidence, status_code=201)
def append_evidence(task_id: str, request: EvidenceCreate) -> Evidence:
    try:
        return store.append_evidence(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc


@router.post("/tasks/{task_id}/tool-executions", response_model=ToolExecution, status_code=201)
def append_tool_execution(task_id: str, request: ToolExecutionCreate) -> ToolExecution:
    try:
        return store.append_tool_execution(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc


@router.post("/tasks/{task_id}/plan-steps", response_model=PlanStep, status_code=201)
def append_plan_step(task_id: str, request: PlanStepCreate) -> PlanStep:
    try:
        return store.append_plan_step(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc


@router.get("/tasks/{task_id}/records")
def task_records(task_id: str) -> list[dict[str, object]]:
    try:
        return store.records(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime task not found") from exc
