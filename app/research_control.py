"""Per-run research consent, accounting and cooperative stopping."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request


@dataclass
class ResearchControl:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    owner_id: int | None = None
    deep: bool = False
    stop_requested: bool = False
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_usage_reports: int = 0
    completed_chunks: int = 0
    documents_attempted: int = 0
    frontier_size: int = 0
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4), repr=False)

    def snapshot(self) -> dict:
        return {"run_id": self.run_id, "deep_research": self.deep,
                "llm_budget": "uncapped" if self.deep else "standard",
                "stop_requested": self.stop_requested, "llm_calls": self.llm_calls,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "llm_usage_reports": self.llm_usage_reports,
                "llm_usage_unknown": max(0, self.llm_calls - self.llm_usage_reports),
                "completed_chunks": self.completed_chunks,
                "documents_attempted": self.documents_attempted, "frontier_size": self.frontier_size, "monetary_cost": "not_reported"}

    def checkpoint(self, key: str, payload: dict) -> None:
        path = Path(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS research_run_checkpoints (
                run_id TEXT, chunk_key TEXT, owner_id INTEGER, created_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (run_id, chunk_key))""")
            db.execute("INSERT OR REPLACE INTO research_run_checkpoints VALUES (?, ?, ?, ?, ?)",
                (self.run_id, key, self.owner_id, datetime.now(timezone.utc).isoformat(),
                 json.dumps(payload, ensure_ascii=False)))


_CURRENT: ContextVar[ResearchControl | None] = ContextVar("research_control", default=None)
CONTROLS: dict[str, ResearchControl] = {}


def current_research() -> ResearchControl | None:
    return _CURRENT.get()


def deep_research_enabled() -> bool:
    value = current_research()
    return bool(value and value.deep)


@contextmanager
def bind_research(control: ResearchControl | None):
    token = _CURRENT.set(control)
    try:
        yield control
    finally:
        _CURRENT.reset(token)


def authorize_research(options, request: Request, *, run_id: str | None = None) -> ResearchControl | None:
    if not options.deep_research:
        return None
    # Uncapped spend is a recorded choice of an authenticated workspace user.
    from app.auth_api import get_auth_provider, _resolve_session, _auth_error, _require_csrf
    if request is None:
        raise HTTPException(status_code=401, detail="authenticated_budget_consent_required")
    resolution = _resolve_session(get_auth_provider(), request.cookies.get("aimeton_session", ""))
    if resolution.failure is not None:
        raise _auth_error(resolution.failure)
    _require_csrf(request.cookies.get("aimeton_csrf"), request.headers.get("X-CSRF-Token"))
    control = ResearchControl(run_id=run_id or uuid4().hex, owner_id=resolution.user.id, deep=True)
    control.checkpoint("consent", {**control.snapshot(), "unlimited_llm_budget_authorized": True})
    CONTROLS[control.run_id] = control
    return control


def record_llm_start() -> None:
    control = current_research()
    if control:
        if control.stop_requested:
            raise ResearchStopped("research_stopped_by_user")
        control.llm_calls += 1


def record_llm_usage(body: dict) -> None:
    control = current_research()
    if control:
        # Missing/malformed usage is unknown, never proof of a free request.
        usage = body.get("usage") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        valid_prompt = type(prompt) is int and prompt >= 0
        valid_completion = type(completion) is int and completion >= 0
        if valid_prompt:
            control.prompt_tokens += prompt
        if valid_completion:
            control.completion_tokens += completion
        if valid_prompt and valid_completion:
            control.llm_usage_reports += 1
        control.checkpoint("usage", control.snapshot())


class ResearchStopped(RuntimeError):
    pass
