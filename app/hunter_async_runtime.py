from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import secrets
from threading import RLock
from typing import Any, Awaitable, Callable
from uuid import uuid4


HunterRunner = Callable[[Callable[[dict[str, object]], None]], Awaitable[dict[str, Any]]]

_LOCK = RLock()
_RUNS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL_STATES = frozenset({"completed", "stopped", "failed"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _public_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    created_at = datetime.fromisoformat(str(record["created_at"]))
    elapsed = max(0.0, (datetime.now(UTC) - created_at).total_seconds())
    return {
        "run_id": record["run_id"],
        "state": record["state"],
        "phase": record["phase"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "elapsed_seconds": round(elapsed, 1),
        "progress": deepcopy(record["progress"]),
        "result": deepcopy(record["result"]),
        "error": record["error"],
    }


def _partial_result(record: dict[str, Any]) -> dict[str, Any]:
    progress = record["progress"]
    candidates = list(progress.get("candidates") or [])
    processed = int(progress.get("processed_candidates") or 0)
    total = int(progress.get("total_candidates") or 0)
    raw_results = int(progress.get("raw_results") or 0)
    return {
        "region": record["region"],
        "search_zone": record["search_zone"],
        "queries": list(progress.get("queries") or []),
        "discovered": total,
        "candidates": candidates,
        "funnel": {
            "raw_results": raw_results,
            "unique_candidates": total,
            "inspected_candidates": processed,
            "qualified_candidates": len(candidates),
            "returned_candidates": len(candidates),
        },
        "notes": [
            "Поиск остановлен пользователем.",
            f"Сохранены результаты завершённых проверок: {processed} из {total}.",
            "Незавершённые проверки не подменялись поверхностными выводами.",
        ],
        "search_regime": {
            "requested": record["requested_regime"],
            "effective": record["requested_regime"] if record["requested_regime"] != "auto" else "balanced",
            "reason": "user_stopped_before_final_classification",
            "routing_changed": False,
            "steering_enabled": False,
        },
    }


async def _execute(run_id: str, runner: HunterRunner) -> None:
    def progress_callback(update: dict[str, object]) -> None:
        with _LOCK:
            record = _RUNS.get(run_id)
            if record is None or record["state"] in _TERMINAL_STATES:
                return
            record["progress"].update(update)
            record["phase"] = str(update.get("phase") or record["phase"])
            record["updated_at"] = _now()

    with _LOCK:
        record = _RUNS[run_id]
        record["state"] = "running"
        record["phase"] = "planning"
        record["updated_at"] = _now()
    try:
        result = await runner(progress_callback)
    except asyncio.CancelledError:
        with _LOCK:
            record = _RUNS[run_id]
            record["state"] = "stopped"
            record["phase"] = "stopped"
            record["updated_at"] = _now()
            record["result"] = _partial_result(record)
        return
    except Exception as exc:
        with _LOCK:
            record = _RUNS[run_id]
            record["state"] = "failed"
            record["phase"] = "failed"
            record["updated_at"] = _now()
            record["error"] = type(exc).__name__
        return
    with _LOCK:
        record = _RUNS[run_id]
        record["state"] = "completed"
        record["phase"] = "completed"
        record["updated_at"] = _now()
        record["result"] = result


def start_hunter_run(
    *,
    region: str,
    search_zone: str | None,
    requested_regime: str,
    runner: HunterRunner,
) -> dict[str, str]:
    run_id = f"hunter-{uuid4().hex}"
    token = secrets.token_urlsafe(32)
    now = _now()
    with _LOCK:
        _RUNS[run_id] = {
            "run_id": run_id,
            "token": token,
            "region": region,
            "search_zone": search_zone,
            "requested_regime": requested_regime,
            "state": "queued",
            "phase": "queued",
            "created_at": now,
            "updated_at": now,
            "progress": {
                "total_queries": 0,
                "completed_queries": 0,
                "total_candidates": 0,
                "processed_candidates": 0,
                "candidates": [],
            },
            "result": None,
            "error": None,
        }
        task = asyncio.create_task(_execute(run_id, runner), name=f"aimeton-hunter:{run_id}")
        _TASKS[run_id] = task
        def release_task(_task: asyncio.Task[None]) -> None:
            with _LOCK:
                _TASKS.pop(run_id, None)

        task.add_done_callback(release_task)
    return {
        "run_id": run_id,
        "run_token": token,
        "state": "queued",
        "status_url": f"/api/hunt/runs/{run_id}",
        "stop_url": f"/api/hunt/runs/{run_id}/stop",
    }


def get_hunter_run(run_id: str, token: str) -> dict[str, Any]:
    with _LOCK:
        record = _RUNS.get(run_id)
        if record is None or not token or not secrets.compare_digest(token, record["token"]):
            raise KeyError(run_id)
        return _public_snapshot(record)


def stop_hunter_run(run_id: str, token: str) -> dict[str, Any]:
    with _LOCK:
        record = _RUNS.get(run_id)
        if record is None or not token or not secrets.compare_digest(token, record["token"]):
            raise KeyError(run_id)
        if record["state"] in _TERMINAL_STATES:
            return _public_snapshot(record)
        record["state"] = "stopped"
        record["phase"] = "stopped"
        record["updated_at"] = _now()
        record["result"] = _partial_result(record)
        task = _TASKS.get(run_id)
        snapshot = _public_snapshot(record)
    if task is not None:
        task.cancel()
    return snapshot


def clear_hunter_runs_for_tests() -> None:
    with _LOCK:
        tasks = list(_TASKS.values())
        _TASKS.clear()
        _RUNS.clear()
    for task in tasks:
        task.cancel()
