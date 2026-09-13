"""Explicit, bounded research turns over a preliminary company profile."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.external_sources import _host
from app.llm import chat_with_routerai
from app.models import ChatRequest, SiteAnalysis
from app.scraper import fetch_site
from app.trace_context import bind_trace_identity
from app.verified_analysis import run_verified_enriched_site_analysis


def refinement_queries(analysis: SiteAnalysis, feedback: str) -> list[tuple[str, str]]:
    # Feedback is a search hint, never official evidence or an execution command.
    focus = " ".join(feedback.split())[:600]
    name = " ".join(analysis.company_name.replace('"', ' ').split())[:180]
    domain = _host(analysis.url)
    return [
        ("official", f'site:{domain} {focus}'),
        ("other", f'"{name}" "{domain}" {focus}'),
        ("registry", f'"{name}" "{domain}" ИНН ОГРН реквизиты филиалы'),
    ]


def merge_profile(previous: SiteAnalysis, fresh: SiteAnalysis, revision_id: str) -> SiteAnalysis:
    result = previous.model_copy(deep=True)
    source_map = {s.id: f"r{revision_id[:12]}-{s.id}" for s in fresh.sources}
    for source in fresh.sources:
        source = source.model_copy(deep=True)
        source.id = source_map[source.id]
        result.sources.append(source)
    by_key = {(f.field, f.value, f.period): f for f in result.company_facts}
    for original in fresh.company_facts:
        fact = original.model_copy(deep=True)
        fact.source_ids = [source_map[s] for s in fact.source_ids if s in source_map]
        key = (fact.field, fact.value, fact.period)
        if key in by_key:
            existing = by_key[key]
            existing.source_ids = list(dict.fromkeys(existing.source_ids + fact.source_ids))
        else:
            if any(f.field == fact.field and f.period == fact.period and f.value != fact.value
                   for f in result.company_facts):
                fact.note = (fact.note + " Различающееся значение сохранено; требуется сверка.").strip()
            result.company_facts.append(fact)
            by_key[key] = fact
    signal_keys = {(s.signal, s.evidence) for s in result.economic_signals}
    for original in fresh.economic_signals:
        if (original.signal, original.evidence) not in signal_keys:
            signal = original.model_copy(deep=True)
            signal.source_ids = [source_map[s] for s in signal.source_ids if s in source_map]
            result.economic_signals.append(signal)
            signal_keys.add((signal.signal, signal.evidence))
    result.evidence = list(dict.fromkeys(result.evidence + fresh.evidence))
    result.risks_and_assumptions = list(dict.fromkeys(result.risks_and_assumptions + fresh.risks_and_assumptions))
    result.research_queries = list(dict.fromkeys(result.research_queries + fresh.research_queries))
    result.research_status = fresh.research_status.copy()
    result.readiness.provider_states.update(fresh.readiness.provider_states)
    result.readiness.client_release_eligible = False
    result.readiness.release_blockers = list(dict.fromkeys(
        result.readiness.release_blockers + ["preliminary_result", "human_review_and_signed_report_required"]
    ))
    return result


def persist_turn(before: SiteAnalysis, after: SiteAnalysis, request: ChatRequest,
                 reply: str, revision_id: str, queries: list[tuple[str, str]]) -> None:
    # Append-only: no lookup by a caller-supplied analysis ID, so this public
    # legacy adapter cannot read another user's conversation from the store.
    path = Path(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "before": before.model_dump(mode="json"), "after": after.model_dump(mode="json"),
        "messages": [m.model_dump() for m in request.messages], "reply": reply,
        "queries": queries, "search_requested": request.refine_search,
    }, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS audit_dialogue_revisions (
            id TEXT PRIMARY KEY, analysis_id TEXT, created_at TEXT NOT NULL,
            payload TEXT NOT NULL, digest TEXT NOT NULL)""")
        connection.execute("INSERT INTO audit_dialogue_revisions VALUES (?, ?, ?, ?, ?)", (
            revision_id, before.analysis_id, datetime.now(timezone.utc).isoformat(), payload, digest,
        ))


async def run_audit_dialogue(request: ChatRequest) -> dict:
    original = request.analysis
    updated = original.model_copy(deep=True)
    revision_id = uuid4().hex
    feedback = next((m.content for m in reversed(request.messages) if m.role == "user"), "").strip()
    if not feedback:
        raise ValueError("user_message_required")
    updated.user_clarifications.append(feedback)
    queries = []
    if request.refine_search:
        queries = refinement_queries(original, feedback)
        try:
            page = await fetch_site(original.url)
            with bind_trace_identity(original.mission_id or f"audit-{revision_id}", revision_id):
                fresh = await run_verified_enriched_site_analysis(
                    page["final_url"], page["title"], page["text"], research_queries=queries,
                )
            updated = merge_profile(updated, fresh, revision_id)
        except Exception as exc:
            updated.research_status = {"stage": "refinement_failed", "error_type": type(exc).__name__}
            updated.risks_and_assumptions.append(
                "Уточняющее исследование не завершено; предыдущий профиль сохранён."
            )
    updated.profile_revision = original.profile_revision + 1
    try:
        reply = await chat_with_routerai(updated, [m.model_dump() for m in request.messages])
    except Exception:
        reply = "Ответ консультанта сейчас недоступен. Уточнение сохранено; профиль и ограничения доступны в отчёте."
    delta = len(updated.company_facts) - len(original.company_facts)
    persist_turn(original, updated, request, reply, revision_id, queries)
    return {"reply": reply, "analysis": updated, "revision_id": revision_id,
            "added_facts": delta, "search_queries": [q for _, q in queries]}
