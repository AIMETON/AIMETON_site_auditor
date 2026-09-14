from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field

from app.llm import BASE_URL, MODEL
from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
    CompanyFact,
    EconomicSignal,
    EvidenceSource,
    PreliminaryResultReadiness,
    PreliminaryVerticalStatus,
    SiteAnalysis,
)


TModel = TypeVar("TModel", bound=BaseModel)


class ProfileExtraction(BaseModel):
    company_name: str
    business_summary: str
    evidence: list[str] = Field(default_factory=list, max_length=12)
    company_facts: list[CompanyFact] = Field(default_factory=list, max_length=30)
    economic_signals: list[EconomicSignal] = Field(default_factory=list, max_length=16)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=16)


class CommercialSynthesis(BaseModel):
    commercial_opportunity: CommercialOpportunity
    agents: list[AgentRecommendation] = Field(min_length=3, max_length=10)
    action_package: ActionPackage


class BusinessMachineSynthesis(BaseModel):
    business_machine_4x4: list[BusinessMachineCell] = Field(
        default_factory=list,
        max_length=16,
    )


class SplitSynthesisPhaseError(RuntimeError):
    def __init__(self, phase: str, error_type: str):
        self.phase = phase
        self.error_type = error_type
        super().__init__(f"routerai_split_phase_failed:{phase}:{error_type}")


class SplitSynthesisPhaseTimeout(TimeoutError):
    def __init__(self, phase: str):
        self.phase = phase
        super().__init__(f"routerai_split_phase_timeout:{phase}")


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    return json.loads(text)


def _fallback_quote(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] if compact else "Текст страницы не извлечён."


async def _request_json(
    phase: str,
    model_type: type[TModel],
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
) -> TModel:
    key = os.getenv("ROUTERAI_API_KEY")
    if not key:
        raise RuntimeError("ROUTERAI_API_KEY не задан")

    schema = json.dumps(model_type.model_json_schema(), ensure_ascii=False)
    payload = {
        "model": MODEL,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"{prompt}\n\nJSON SCHEMA:\n{schema}",
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        content = choice["message"]["content"]
        return model_type.model_validate(_extract_json(content))
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise SplitSynthesisPhaseTimeout(phase) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise SplitSynthesisPhaseError(phase, type(exc).__name__) from exc


_KM_CANON = {
    "I-I": ("I — Коммуникационные системы", "Взаимодействие"),
    "I-II": ("I — Коммуникационные системы", "Влияние"),
    "I-III": ("I — Коммуникационные системы", "Зависимость"),
    "I-IV": ("I — Коммуникационные системы", "Противодействие"),
    "II-I": ("II — Люди", "Учредители и собственники"),
    "II-II": ("II — Люди", "Ось люди-управленцы"),
    "II-III": ("II — Люди", "Обслуживающий персонал и роботы"),
    "II-IV": ("II — Люди", "Виртуозы и специалисты"),
    "III-I": ("III — Технологии", "Знания и наука"),
    "III-II": ("III — Технологии", "Стандартная процедура"),
    "III-III": ("III — Технологии", "Рабочая процедура"),
    "III-IV": ("III — Технологии", "Продукты, товар и услуга"),
    "IV-I": ("IV — Менеджмент", "Управление коммуникационными системами"),
    "IV-II": ("IV — Менеджмент", "Управление людьми"),
    "IV-III": ("IV — Менеджмент", "Управление технологиями"),
    "IV-IV": ("IV — Менеджмент", "Самоуправление"),
}


def _normalize_km_cells(cells: list[BusinessMachineCell]) -> list[BusinessMachineCell]:
    normalized: list[BusinessMachineCell] = []
    seen: set[str] = set()
    for cell in cells:
        if cell.code in seen:
            continue
        seen.add(cell.code)
        operator, vertex = _KM_CANON[cell.code]
        data = cell.model_dump()
        data["detail_operator"] = operator
        data["vertex"] = vertex
        normalized.append(BusinessMachineCell.model_validate(data))
    return normalized[:16]


def _safe_source_type(value: Any) -> str:
    allowed = {
        "official_page", "registry", "court", "arbitration", "enforcement",
        "ownership", "affiliation", "finance", "workforce", "contact",
        "news", "social", "review", "jobs", "tender", "patent",
        "external_source", "visual_observation",
    }
    text = str(value or "external_source")
    return text if text in allowed else "external_source"


def _safe_evidence_level(value: Any) -> str:
    allowed = {"confirmed_fact", "corroborated_signal", "weak_signal", "unverified_mention"}
    text = str(value or "unverified_mention")
    return text if text in allowed else "unverified_mention"


def _referenced_source_ids(
    profile: ProfileExtraction,
    km: BusinessMachineSynthesis,
) -> set[str]:
    ids = {"S1"}
    for fact in profile.company_facts:
        ids.update(fact.source_ids)
    for signal in profile.economic_signals:
        ids.update(signal.source_ids)
    for cell in km.business_machine_4x4:
        ids.update(cell.source_ids)
    return ids


def _build_sources(
    *,
    title: str,
    url: str,
    text: str,
    accessed_at: str,
    external_sources: list[dict[str, Any]],
    referenced_ids: set[str],
) -> list[EvidenceSource]:
    sources = [
        EvidenceSource(
            id="S1",
            title=title or url,
            url=url,
            accessed_at=accessed_at,
            evidence_quote=_fallback_quote(text),
            source_type="official_page",
            evidence_level="confirmed_fact",
        )
    ]
    seen = {"S1"}
    for item in external_sources:
        source_id = str(item.get("id") or "")
        if (not source_id or source_id in seen or source_id not in referenced_ids
                or item.get("lifecycle_state") != "evidence"
                or not item.get("evidence_digest")):
            continue
        sources.append(
            EvidenceSource(
                id=source_id,
                title=str(item.get("title") or item.get("url") or source_id),
                url=str(item.get("url") or url),
                accessed_at=str(item.get("accessed_at") or accessed_at),
                evidence_quote=str(
                    item.get("evidence_quote")
                    or item.get("snippet")
                    or "Поисковый сниппет без подтверждённой цитаты."
                ),
                source_type=_safe_source_type(item.get("source_type")),
                evidence_level=_safe_evidence_level(item.get("evidence_level")),
                document_url=item.get("document_url"),
                document_title=item.get("document_title"),
                document_accessed_at=item.get("document_accessed_at"),
                document_digest=item.get("document_digest"),
                evidence_locator=item.get("evidence_locator"),
                evidence_digest=item.get("evidence_digest"),
                fetch_path=item.get("fetch_path"),
            )
        )
        seen.add(source_id)
    return sources


def _readiness(
    *,
    company_facts: list[CompanyFact],
    sources: list[EvidenceSource],
    commercial_score: int,
) -> PreliminaryResultReadiness:
    fact_fields = {item.field for item in company_facts if item.source_ids}
    vertical_fields = {
        "identity": {"legal_name", "brand_name", "inn", "ogrn", "registration_status", "address"},
        "contacts": {"phones", "emails", "website", "social_accounts"},
        "workforce": {"headcount"},
        "financials": {"revenue", "profit", "assets", "taxes"},
        "ownership": {"founders", "executives", "beneficial_owners", "affiliates"},
        "legal_events": set(),
    }
    document_keys = {source.document_url or source.url for source in sources}
    confirmed_documents = {
        source.document_url or source.url for source in sources
        if source.evidence_level in {"confirmed_fact", "corroborated_signal"}
        and source.document_digest is not None
        and source.evidence_digest is not None
    }
    return PreliminaryResultReadiness(
        analysis_state="schema_validated",
        profile_completeness=min(len(fact_fields) / 25, 1),
        evidence_quality=(len(confirmed_documents) / len(document_keys) if document_keys else 0),
        commercial_priority=commercial_score,
        required_verticals=[
            PreliminaryVerticalStatus(
                code=code,
                state=("partially_verified" if fields and fact_fields.intersection(fields) else "not_searched"),
            )
            for code, fields in vertical_fields.items()
        ],
        provider_states={"routerai": "active"},
        release_blockers=[
            "preliminary_result",
            "identity_unresolved",
            "sufficiency_below_l4",
            "mandatory_verticals_incomplete",
            "provider_state_unknown",
            "budget_unknown",
            "human_review_and_signed_report_required",
        ],
    )


def _assemble_site_analysis(
    *,
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]],
    profile: ProfileExtraction,
    km: BusinessMachineSynthesis,
    commercial: CommercialSynthesis,
    accessed_at: str,
) -> SiteAnalysis:
    km_cells = _normalize_km_cells(km.business_machine_4x4)
    referenced_ids = _referenced_source_ids(
        profile,
        BusinessMachineSynthesis(business_machine_4x4=km_cells),
    )
    sources = _build_sources(
        title=title,
        url=url,
        text=text,
        accessed_at=accessed_at,
        external_sources=external_sources,
        referenced_ids=referenced_ids,
    )
    known_ids = {source.id for source in sources}

    for fact in profile.company_facts:
        fact.source_ids = [source_id for source_id in fact.source_ids if source_id in known_ids]
    for signal in profile.economic_signals:
        signal.source_ids = [source_id for source_id in signal.source_ids if source_id in known_ids]
    for cell in km_cells:
        cell.source_ids = [source_id for source_id in cell.source_ids if source_id in known_ids]

    return SiteAnalysis(
        research_status={"extraction_input_coverage_complete": bool(getattr(profile, "coverage", {}).get("complete", False))},
        url=url,
        company_name=profile.company_name or title or url,
        business_summary=profile.business_summary,
        evidence=profile.evidence,
        sources=sources,
        company_facts=profile.company_facts,
        business_machine_4x4=km_cells,
        economic_signals=profile.economic_signals,
        commercial_opportunity=commercial.commercial_opportunity,
        agents=commercial.agents,
        action_package=commercial.action_package,
        risks_and_assumptions=profile.risks_and_assumptions,
        readiness=_readiness(
            company_facts=profile.company_facts,
            sources=sources,
            commercial_score=commercial.commercial_opportunity.score,
        ),
    )


async def analyze_with_routerai_split(
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]] | None = None,
) -> SiteAnalysis:
    """Extract evidence once, then reason in parallel and assemble deterministically."""
    external_sources = external_sources or []
    accessed_at = datetime.now(timezone.utc).isoformat()
    external_context = json.dumps(external_sources, ensure_ascii=False, separators=(",", ":"))[:52000]

    profile_prompt = f"""Ты — модуль доказательного извлечения AIMETON Site Auditor.
Не продавай и не строй КМ на этом этапе. Извлеки только профиль компании, факты и экономические сигналы.

Правила доказательности:
- S1 — официальный сайт; внешние источники имеют переданные id.
- Не создавай URL, цифры, людей, контакты и факты, отсутствующие во входных данных.
- Поисковый сниппет — только сигнал, не подтверждённый первичный документ.
- Каждый company_fact и economic_signal должен содержать только реально поддерживающие source_ids.
- Не найдено — не выдумывай. Финансовые значения сопровождай периодом, если он известен.
- Разделяй факты, сигналы и риски.
- Пиши кратко: evidence не более 12 пунктов, company_facts не более 30, economic_signals не более 16, risks_and_assumptions не более 16.

OFFICIAL URL: {url}
TITLE: {title}
ACCESSED AT: {accessed_at}
OFFICIAL PAGE TEXT:
{text[:30000]}

EXTERNAL OSINT SOURCES:
{external_context}
"""
    profile = await _request_json(
        "profile_extraction",
        ProfileExtraction,
        system="Возвращай только валидный JSON по схеме. Не выдумывай отсутствующие данные.",
        prompt=profile_prompt,
        max_tokens=3200,
        timeout_seconds=28.0,
    )

    profile_context = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))

    km_prompt = f"""Построй каноническую бизнес-модель AIMETON / КМ только из извлечённого профиля ниже.
Сформируй до 16 ячеек. Не создавай факты сверх профиля. Для каждой ячейки укажи finding, status, confidence, source_ids и sales_relevance.

Канон:
I-I Взаимодействие; I-II Влияние; I-III Зависимость; I-IV Противодействие.
II-I Учредители и собственники; II-II Ось люди-управленцы; II-III Обслуживающий персонал и роботы; II-IV Виртуозы и специалисты.
III-I Знания и наука; III-II Стандартная процедура; III-III Рабочая процедура; III-IV Продукты, товар и услуга.
IV-I Управление коммуникационными системами; IV-II Управление людьми; IV-III Управление технологиями; IV-IV Самоуправление.
Если данных нет, используй status=\"Нет данных\" и не компенсируй пробелы фантазией.

EXTRACTED PROFILE:
{profile_context}
"""

    commercial_prompt = f"""Ты — AI-продажник AIMETON. На основе только извлечённого профиля выбери одну наиболее доказанную коммерческую AI-возможность.
Сначала учитывай подтверждённые экономические сигналы и пробелы, затем предложи реалистичное решение, 3–10 AI-агентов/инструментов и пакет первого контакта.
Оценка 80+ допустима только при прямом подтверждении проблемы, масштаба и реалистичного пилота. Не обещай неподтверждённый эффект.

EXTRACTED PROFILE:
{profile_context}
"""

    km_result, commercial_result = await asyncio.gather(
        _request_json(
            "km_reasoning",
            BusinessMachineSynthesis,
            system="Возвращай только валидный JSON по схеме и соблюдай канонические коды КМ.",
            prompt=km_prompt,
            max_tokens=3200,
            timeout_seconds=26.0,
        ),
        _request_json(
            "commercial_reasoning",
            CommercialSynthesis,
            system="Возвращай только валидный JSON по схеме. Главный результат — доказанная коммерческая возможность.",
            prompt=commercial_prompt,
            max_tokens=1800,
            timeout_seconds=26.0,
        ),
    )

    return _assemble_site_analysis(
        url=url,
        title=title,
        text=text,
        external_sources=external_sources,
        profile=profile,
        km=km_result,
        commercial=commercial_result,
        accessed_at=accessed_at,
    )
