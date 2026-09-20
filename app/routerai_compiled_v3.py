from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
    CompanyFact,
    EconomicSignal,
    SiteAnalysis,
)
from app.profile_consolidation import consolidate_merged_profile
from app.research_control import ResearchStopped, current_research
from app.routerai_evidence_ledger import persist_merged_evidence_ledger
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import (
    CompactCompanyFact,
    CompactEconomicSignal,
    MergedProfileExtraction,
)
from app.routerai_split_synthesis import (
    BusinessMachineSynthesis,
    CommercialSynthesis,
    _assemble_site_analysis,
)
from app.routerai_split_v2 import (
    _full_reasoning_profile,
    _reasoning_source_authority,
    _reasoning_source_groups,
    _unavailable_commercial,
)
from app.routerai_strict_request import request_json_strict


MAX_COMPILED_CONTEXT_CHARS = 120_000
MAX_ROOT_TEXT_CHARS = 18_000
MAX_DOCUMENT_CONTEXT_CHARS = 6_000
MIN_DOCUMENT_CONTEXT_CHARS = 900

_CHILD_SOURCE_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")

_EVIDENCE_RANK = {
    "confirmed_fact": 3,
    "corroborated_signal": 2,
    "weak_signal": 1,
    "unverified_mention": 0,
}


class CompiledProfileResponse(BaseModel):
    company_name: str = Field(max_length=180)
    business_summary: str = Field(max_length=700)
    evidence: list[str] = Field(default_factory=list, max_length=16)
    company_facts: list[CompactCompanyFact] = Field(default_factory=list, max_length=140)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=40)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=24)


class CompiledSynthesisResponse(BaseModel):
    business_machine_4x4: list[BusinessMachineCell] = Field(default_factory=list, max_length=16)
    commercial_opportunity: CommercialOpportunity
    agents: list[AgentRecommendation] = Field(min_length=3, max_length=5)
    action_package: ActionPackage


@dataclass(frozen=True)
class CompiledContextStats:
    input_records: int
    document_groups: int
    context_chars: int
    truncated: bool

    def safe_dict(self) -> dict[str, int | bool]:
        return {
            "input_records": self.input_records,
            "document_groups": self.document_groups,
            "context_chars": self.context_chars,
            "truncated": self.truncated,
        }


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _source_group_key(source: dict[str, Any], index: int) -> str:
    source_id = _compact_text(source.get("id"))
    if source_id:
        match = _CHILD_SOURCE_ID.match(source_id)
        return match.group("parent") if match else source_id
    url = _compact_text(source.get("document_url") or source.get("url"))
    return url or f"source-{index}"


def _merge_document_records(external_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, source in enumerate(external_sources):
        key = _source_group_key(source, index)
        if key not in grouped:
            grouped[key] = {
                "id": key,
                "url": _compact_text(source.get("document_url") or source.get("url")),
                "source_class": _compact_text(source.get("source_class")) or "unknown",
                "evidence_level": _compact_text(source.get("evidence_level")) or "unverified_mention",
                "query_kinds": [],
                "snippets": [],
            }
            order.append(key)
        target = grouped[key]
        query_kind = _compact_text(source.get("query_kind")) or "unknown"
        if query_kind not in target["query_kinds"]:
            target["query_kinds"].append(query_kind)
        level = _compact_text(source.get("evidence_level")) or "unverified_mention"
        if _EVIDENCE_RANK.get(level, 0) > _EVIDENCE_RANK.get(target["evidence_level"], 0):
            target["evidence_level"] = level
        snippet = _compact_text(source.get("evidence_quote") or source.get("snippet"))
        if snippet and snippet.casefold() not in {item.casefold() for item in target["snippets"]}:
            target["snippets"].append(snippet)

    result: list[dict[str, Any]] = []
    for key in order:
        item = grouped[key]
        result.append({
            "id": item["id"],
            "url": item["url"],
            "source_class": item["source_class"],
            "evidence_level": item["evidence_level"],
            "query_kinds": sorted(item["query_kinds"]),
            "text": "\n".join(item["snippets"]),
        })
    result.sort(
        key=lambda item: (
            -_EVIDENCE_RANK.get(str(item.get("evidence_level") or ""), 0),
            str(item.get("id") or ""),
        )
    )
    return result


def compile_company_context(
    *,
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]],
) -> tuple[str, CompiledContextStats]:
    """Compile the evidence corpus once for a single profile extraction call.

    The persistent/raw evidence ledger is untouched. This projection groups repeated
    model-facing records by stable document/source id and applies a fair bounded text
    budget so every document remains represented.
    """
    records = _merge_document_records(external_sources)
    root = _compact_text(text)[:MAX_ROOT_TEXT_CHARS]
    base = {
        "official_url": url,
        "title": title,
        "official_root_text": root,
        "documents": [],
    }
    fixed_chars = len(json.dumps(base, ensure_ascii=False, separators=(",", ":")))
    remaining = max(0, MAX_COMPILED_CONTEXT_CHARS - fixed_chars)
    truncated = len(_compact_text(text)) > len(root)

    compiled_docs: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        docs_left = max(1, len(records) - index)
        fair_share = max(MIN_DOCUMENT_CONTEXT_CHARS, remaining // docs_left)
        allowance = min(MAX_DOCUMENT_CONTEXT_CHARS, fair_share)
        raw_text = str(record.get("text") or "")
        bounded = raw_text[:allowance]
        if len(raw_text) > len(bounded):
            truncated = True
        doc = {**record, "text": bounded}
        compiled_docs.append(doc)
        cost = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        remaining = max(0, remaining - cost)

    payload = {**base, "documents": compiled_docs}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > MAX_COMPILED_CONTEXT_CHARS:
        serialized = serialized[:MAX_COMPILED_CONTEXT_CHARS]
        truncated = True
    return serialized, CompiledContextStats(
        input_records=len(external_sources),
        document_groups=len(records),
        context_chars=len(serialized),
        truncated=truncated,
    )


def _profile_prompt(context: str) -> str:
    return f"""Ты единый Profile Compiler AIMETON. Ниже уже собран и дедуплицирован
доказательный пакет компании. Выполни один согласованный проход по всему контексту.

Задача:
- восстановить нормализованный профиль компании;
- извлечь только независимые проверяемые факты;
- каждый факт и сигнал привязать только к реально поддерживающим source_ids;
- одинаковую услугу не размножать из-за цены, акции, страницы или варианта формулировки;
- founders/executives/beneficial_owners/affiliates/customers/suppliers включать только
  при прямом подтверждении соответствующей роли и реальном source_id;
- использование бренда, оборудования или технологии не означает supplier/customer relation;
- учредитель/директор не является автоматически beneficial_owner;
- неизвестное не выдумывать;
- финансовые значения разделять по показателю и периоду;
- evidence — только короткие опорные тезисы, а не копия контекста.

Это единственный extraction pass: рассматривай документы совместно, разрешай повторы и
противоречия на уровне профиля, а не по отдельным чанкам.

COMPILED COMPANY CONTEXT:
{context}
"""


def _synthesis_prompt(
    *,
    profile_context: str,
    source_authority: dict[str, str],
    source_groups: dict[str, str],
) -> str:
    return f"""Ты единый multi-role reasoning модуль AIMETON. Работай одновременно как
business analyst, ownership/management analyst и AI-sales architect. На входе уже
нормализованный CompanyProfile; сырой HTML повторно не анализируй.

За один structured pass:
1. Построй каноническую КМ 4x4 (до 16 ячеек).
2. Выбери ровно одну наиболее доказанную commercial_opportunity.
3. Дай 3–5 конкретных AI-инструментов/агентов.
4. Сформируй action_package первого контакта.

Правила:
- ничего не добавляй сверх профиля;
- source_ids должны существовать в профиле;
- страницы одного origin не считаются независимой corroboration;
- confirmed_fact > corroborated_signal > weak_signal;
- score 80+ только при прямом доказательстве проблемы, масштаба и реалистичного пилота;
- expected_value не должен содержать неподтверждённые числовые обещания;
- если данных для ячейки КМ нет, status="Нет данных";
- не переинтерпретируй роли людей и организаций без прямого evidence.

NORMALIZED COMPANY PROFILE:
{profile_context}

SOURCE AUTHORITY:
{json.dumps(source_authority, ensure_ascii=False, separators=(",", ":"))}

SOURCE ORIGIN GROUPS:
{json.dumps(source_groups, ensure_ascii=False, separators=(",", ":"))}
"""


async def analyze_with_routerai_compiled_v3(
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]] | None = None,
) -> SiteAnalysis:
    """Deterministic context compiler -> one profile call -> one synthesis call."""
    external_sources = external_sources or []
    if current_research() and current_research().stop_requested:
        raise ResearchStopped("research_stopped_by_user")

    context, context_stats = compile_company_context(
        url=url,
        title=title,
        text=text,
        external_sources=external_sources,
    )
    extracted = await request_json_strict(
        "profile_compiled_extraction",
        CompiledProfileResponse,
        system=(
            "Возвращай только валидный компактный JSON по схеме. "
            "Это единый extraction pass по уже собранному evidence context."
        ),
        prompt=_profile_prompt(context),
        max_tokens=12_000,
        timeout_seconds=120.0,
        reasoning_enabled=False,
    )

    coverage = EvidenceCoverage(
        official_chars_total=len(text),
        official_chunks_total=1,
        official_chunks_processed=1,
        sources_total=len(external_sources),
        sources_processed=len(external_sources),
        source_chunks_total=context_stats.document_groups,
        source_chunks_processed=context_stats.document_groups,
        extraction_units_total=1,
        extraction_units_processed=1,
        complete=True,
    )
    raw = MergedProfileExtraction(
        company_name=extracted.company_name or title,
        business_summary=extracted.business_summary,
        evidence=list(extracted.evidence),
        company_facts=[
            CompanyFact.model_validate(item.model_dump(mode="python"))
            for item in extracted.company_facts
        ],
        economic_signals=[
            EconomicSignal.model_validate(item.model_dump(mode="python"))
            for item in extracted.economic_signals
        ],
        risks_and_assumptions=list(extracted.risks_and_assumptions),
        coverage=coverage,
    )
    persist_merged_evidence_ledger(raw)
    merged, consolidation = consolidate_merged_profile(
        raw,
        external_sources=external_sources,
    )
    profile = _full_reasoning_profile(merged)
    profile_context = json.dumps(
        {
            "company_name": profile.company_name,
            "business_summary": profile.business_summary,
            "company_facts": [item.model_dump(mode="json") for item in profile.company_facts],
            "economic_signals": [item.model_dump(mode="json") for item in profile.economic_signals],
            "risks_and_assumptions": profile.risks_and_assumptions,
            "coverage": profile.coverage,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        if current_research() and current_research().stop_requested:
            raise ResearchStopped("research_stopped_by_user")
        synthesized = await request_json_strict(
            "compiled_business_commercial_synthesis",
            CompiledSynthesisResponse,
            system=(
                "Возвращай только валидный JSON по схеме. "
                "Это единый reasoning/synthesis pass по нормализованному профилю."
            ),
            prompt=_synthesis_prompt(
                profile_context=profile_context,
                source_authority=_reasoning_source_authority(external_sources),
                source_groups=_reasoning_source_groups(url, external_sources),
            ),
            max_tokens=6_000,
            timeout_seconds=120.0,
            reasoning_enabled=False,
        )
        result = _assemble_site_analysis(
            url=url,
            title=title,
            text=text,
            external_sources=external_sources,
            profile=profile,
            km=BusinessMachineSynthesis(
                business_machine_4x4=synthesized.business_machine_4x4
            ),
            commercial=CommercialSynthesis(
                commercial_opportunity=synthesized.commercial_opportunity,
                agents=synthesized.agents,
                action_package=synthesized.action_package,
            ),
            accessed_at=datetime.now(timezone.utc).isoformat(),
        )
        synthesis_state = "succeeded"
        synthesis_calls = 1
    except ResearchStopped:
        raise
    except Exception as exc:
        result = _assemble_site_analysis(
            url=url,
            title=title,
            text=text,
            external_sources=external_sources,
            profile=profile,
            km=BusinessMachineSynthesis(),
            commercial=_unavailable_commercial(),
            accessed_at=datetime.now(timezone.utc).isoformat(),
        )
        result.readiness.provider_states["routerai"] = "reasoning_failed_extraction_preserved"
        result.readiness.analysis_state = "preliminary_hypothesis"
        if "commercial_reasoning_incomplete" not in result.readiness.release_blockers:
            result.readiness.release_blockers.append("commercial_reasoning_incomplete")
        result.risks_and_assumptions.append(
            f"Compiled synthesis не завершён ({type(exc).__name__}); "
            "нормализованный профиль и evidence сохранены."
        )
        synthesis_state = "failed"
        synthesis_calls = 1

    result.research_status.update({
        "analysis_orchestration": "compiled_v3_two_call",
        "compiled_context": context_stats.safe_dict(),
        "profile_consolidation": consolidation.safe_dict(),
        "extraction_coverage": profile.coverage,
        "core_llm_profile_calls": 1,
        "core_llm_synthesis_calls": synthesis_calls,
        "core_llm_calls": 1 + synthesis_calls,
        "commercial_reasoning_state": synthesis_state,
        "commercial_score_available": synthesis_state == "succeeded",
    })
    return result
