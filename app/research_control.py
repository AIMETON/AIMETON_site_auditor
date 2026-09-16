"""Per-run research consent, accounting and cooperative stopping."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from app.research_settings import ResearchSettings, ResearchSettingsRepository, SettingsConflict


@dataclass
class ResearchControl:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    owner_id: int | None = None
    deep: bool = False
    settings: ResearchSettings | None = None
    settings_revision: int | None = None
    settings_service: str = "site-audit"
    settings_digest: str | None = None
    stop_reason: str | None = None
    progress_warning: bool = False
    stop_requested: bool = False
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_usage_reports: int = 0
    search_attempts: int = 0
    search_price_unknown: int = 0
    search_estimates: dict[str, Decimal] = field(default_factory=dict)
    completed_chunks: int = 0
    documents_attempted: int = 0
    frontier_size: int = 0
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4), repr=False)

    def spending_status(self) -> dict:
        thresholds = {}
        if self.settings:
            total = self.prompt_tokens + self.completion_tokens
            for name in ("token_warning", "token_limit"):
                value = getattr(self.settings, name)
                if value is not None:
                    thresholds[name] = "reached" if total >= value else "not_reached"
            for name in ("cost_warning_amount", "cost_limit_amount"):
                if getattr(self.settings, name) is not None:
                    thresholds[name] = "unknown"
        return {"spending_policy": "account_only", "monetary_limit_enforced": False,
                "token_limit_enforced": False, "spending_thresholds": thresholds,
                "search_attempts": self.search_attempts,
                "search_price_unknown": self.search_price_unknown,
                "search_cost_estimate_by_currency": {key: str(value) for key, value in self.search_estimates.items()},
                "search_cost_basis": "configured_tariff_per_dispatched_attempt"}

    def snapshot(self) -> dict:
        return {**self.spending_status(), "run_id": self.run_id, "deep_research": self.deep,
                "llm_budget": "uncapped" if self.deep else "standard",
                "stop_requested": self.stop_requested, "llm_calls": self.llm_calls,
                **({"settings_revision": self.settings_revision, "settings_digest": self.settings_digest or "",
                    "stop_reason": self.stop_reason or "", "progress_warning": self.progress_warning}
                   if self.settings is not None else {}),
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
            # A dedicated snapshot avoids choosing between incompatible checkpoint
            # payloads or relying on wall-clock ordering after a restart.
            db.execute("INSERT OR REPLACE INTO research_run_checkpoints VALUES (?, ?, ?, ?, ?)",
                (self.run_id, "status", self.owner_id, datetime.now(timezone.utc).isoformat(),
                 json.dumps(self.snapshot(), ensure_ascii=False)))


_CURRENT: ContextVar[ResearchControl | None] = ContextVar("research_control", default=None)
CONTROLS: dict[str, ResearchControl] = {}


def bind_analysis_control(control: ResearchControl | None, analysis_id: str) -> None:
    """Persist the analysis/run association before scheduling any provider work."""
    if control is None:
        return
    control.checkpoint("bound", control.snapshot())
    path = os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS research_analysis_runs "
                   "(analysis_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner_id INTEGER)")
        db.execute("INSERT OR IGNORE INTO research_analysis_runs VALUES (?, ?, ?)",
                   (analysis_id, control.run_id, control.owner_id))
        existing = db.execute("SELECT run_id, owner_id FROM research_analysis_runs WHERE analysis_id=?",
                              (analysis_id,)).fetchone()
        if existing != (control.run_id, control.owner_id):
            raise SettingsConflict("analysis_research_binding_conflict")
    CONTROLS[analysis_id] = control


def recovered_research_snapshot(analysis_id: str) -> dict | None:
    """Read last observed counters only; never resume work or reconstruct consent."""
    path = Path(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")).resolve()
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("""SELECT c.payload, c.created_at
                FROM research_analysis_runs a JOIN research_run_checkpoints c
                ON c.run_id=a.run_id AND c.owner_id IS a.owner_id
                WHERE a.analysis_id=? AND c.chunk_key='status'""", (analysis_id,)).fetchone()
        if row is None:
            return None
        saved = json.loads(row[0])
        if not isinstance(saved, dict):
            return None
        # Only fields already exposed by live status; never checkpoint settings,
        # owner identifiers, provider bodies, prompts or arbitrary added payloads.
        allowed = ResearchControl(settings=ResearchSettings()).snapshot().keys()
        return {**{key: saved[key] for key in allowed if key in saved},
                "accounting_recovered": True, "accounting_checkpoint_at": row[1]}
    except (sqlite3.Error, ValueError, OSError):
        return None


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


def authorize_research(options, request: Request, *, run_id: str | None = None, service: str = "site-audit", use_saved_settings: bool = True) -> ResearchControl | None:
    revision = getattr(options, "research_settings_revision", None)
    deep = getattr(options, "deep_research", False)
    if not deep and revision is None:
        if not use_saved_settings or request is None or not request.cookies.get("aimeton_session"):
            return None
        from app.auth_api import get_auth_provider, _resolve_session, _auth_error
        session = _resolve_session(get_auth_provider(), request.cookies.get("aimeton_session", ""))
        if session.failure is not None:
            raise _auth_error(session.failure)
        saved = ResearchSettingsRepository(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")).get(session.user.id, service)
        if saved.revision == 0:
            return None
        revision = saved.revision
    # Uncapped spend is a recorded choice of an authenticated workspace user.
    from app.auth_api import get_auth_provider, _resolve_session, _auth_error, _require_csrf
    if request is None:
        raise HTTPException(status_code=401, detail="authenticated_budget_consent_required")
    resolution = _resolve_session(get_auth_provider(), request.cookies.get("aimeton_session", ""))
    if resolution.failure is not None:
        raise _auth_error(resolution.failure)
    _require_csrf(request.cookies.get("aimeton_csrf"), request.headers.get("X-CSRF-Token"))
    control = ResearchControl(run_id=run_id or uuid4().hex, owner_id=resolution.user.id, deep=deep)
    if revision is None and use_saved_settings:
        saved = ResearchSettingsRepository(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")).get(resolution.user.id, service)
        if saved.revision > 0:
            revision = saved.revision
    if revision is not None:
        from app.research_execution import compile_policy, PolicyUnavailable
        record = ResearchSettingsRepository(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")).get(resolution.user.id, service)
        if record.revision != revision:
            raise HTTPException(status_code=409, detail="settings_revision_conflict")
        try:
            compile_policy(record.settings)
        except PolicyUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        control.settings = record.settings
        control.settings_revision = revision
        control.settings_service = service
    control.checkpoint("consent", {**control.snapshot(), "unlimited_llm_budget_authorized": deep})
    CONTROLS[control.run_id] = control
    return control


def bind_settings_snapshot(control, mission_id: str, analysis_id: str) -> None:
    if control is None or control.settings is None:
        return
    try:
        saved = ResearchSettingsRepository(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")).snapshot(
            control.owner_id, control.settings_service, mission_id, analysis_id,
            expected_revision=control.settings_revision, enforce=True)
    except SettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    control.settings_digest = saved["digest"]
    control.checkpoint("settings", saved)


def record_search_attempt(provider) -> None:
    """Persist a tariff estimate before dispatch, including failed/cancelled attempts.

    This is not a billing receipt. Cache hits and denied/reserved-only work do not
    call this hook. Unknown pricing remains separate from known zero-cost work.
    """
    control = current_research()
    if control is None:
        return
    control.search_attempts += 1
    amount, currency = provider.cost_amount, provider.cost_currency
    known = (isinstance(amount, Decimal) and amount.is_finite() and amount >= 0
             and isinstance(currency, str) and bool(currency)
             and (not provider.paid or amount > 0))
    if known:
        control.search_estimates[currency] = control.search_estimates.get(currency, Decimal(0)) + amount
    else:
        control.search_price_unknown += 1
    control.checkpoint("search_usage", control.snapshot())


def record_llm_start() -> None:
    from app.research_execution import check_execution
    check_execution()
    control = current_research()
    if control:
        if control.stop_requested:
            raise ResearchStopped("research_stopped_by_user")
        control.llm_calls += 1
        control.checkpoint("llm_started", control.snapshot())


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
