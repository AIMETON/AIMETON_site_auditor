from __future__ import annotations

from app.research_execution import active_settings

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.research_control import deep_research_enabled, current_research, ResearchStopped
from app.models import (
    ActionPackage,
    BusinessMachineCell,
    CommercialOpportunity,
    CompanyFact,
    EconomicSignal,
    SiteAnalysis,
)
from app.profile_consolidation import consolidate_merged_profile
from app.reasoning_dossier import ReasoningDossier, build_reasoning_dossier
from app.routerai_evidence_ledger import persist_merged_evidence_ledger
from app.routerai_profile_extraction import extract_profile_parallel
from app.routerai_split_synthesis import (
    BusinessMachineSynthesis,
    CommercialSynthesis,
    _assemble_site_analysis,
    _request_json,
)
from app.routerai_strict_request import request_json_strict


CompactText80 = Annotated[str, Field(max_length=80)]
CompactText140 = Annotated[str, Field(max_length=140)]
CompactText180 = Annotated[str, Field(max_length=180)]
CompactText220 = Annotated[str, Field(max_length=220)]


class FullReasoningProfile(BaseModel):
    """Internal split-v2 profile: preserve the complete merged evidence ledger."""

    company_name: str
    business_summary: str
    evidence: list[str] = Field(default_factory=list)
    company_facts: list[CompanyFact] = Field(default_factory=list)
    economic_signals: list[EconomicSignal] = Field(default_factory=list)
    risks_and_assumptions: list[str] = Field(default_factory=list)
    coverage: dict[str, int | bool] = Field(default_factory=dict)


class CompactBusinessMachineQuadrant(BaseModel):
    business_machine_4x4: list[BusinessMachineCell] = Field(default_factory=list, max_length=4)


class CompactBusinessMachineCell(BaseModel):
    business_machine_4x4: list[BusinessMachineCell] = Field(default_factory=list, max_length=1)


class CompactCommercialOpportunity(BaseModel):
    opportunity_type: CompactText80
    problem_hypothesis: CompactText220
    recommended_solution: CompactText220
    expected_value: CompactText180
    score: int = Field(ge=0, le=100)
    qualification: Literal["Приоритетная", "Перспективная", "Наблюдение", "Недостаточно данных"]


class CompactAgentRecommendation(BaseModel):
    name: CompactText80
    purpose: CompactText140
    benefit: CompactText140
    priority: Literal["Высокий", "Средний", "Низкий"] = "Средний"


class CompactActionPackage(BaseModel):
    decision_maker_hypothesis: CompactText140
    contact_reason: CompactText180
    demo_scenario: list[CompactText140] = Field(default_factory=list, max_length=3)
    first_message: CompactText220
    next_action: CompactText140


class CompactCommercialExecution(BaseModel):
    agents: list[CompactAgentRecommendation] = Field(min_length=3, max_length=5)
    action_package: CompactActionPackage


_KM_QUADRANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("I", ("I-I", "I-II", "I-III", "I-IV")),
    ("II", ("II-I", "II-II", "II-III", "II-IV")),
    ("III", ("III-I", "III-II", "III-III", "III-IV")),
    ("IV", ("IV-I", "IV-II", "IV-III", "IV-IV")),
)

_KM_LABELS = {
    "I-I": "Взаимодействие",
    "I-II": "Влияние",
    "I-III": "Зависимость",
    "I-IV": "Противодействие",
    "II-I": "Учредители и собственники",
    "II-II": "Ось люди-управленцы",
    "II-III": "Обслуживающий персонал и роботы",
    "II-IV": "Виртуозы и специалисты",
    "III-I": "Знания и наука",
    "III-II": "Стандартная процедура",
    "III-III": "Рабочая процедура",
    "III-IV": "Продукты, товар и услуга",
    "IV-I": "Управление коммуникационными системами",
    "IV-II": "Управление людьми",
    "IV-III": "Управление технологиями",
    "IV-IV": "Самоуправление",
}


def _expand_commercial(opportunity: CompactCommercialOpportunity, execution: CompactCommercialExecution) -> CommercialSynthesis:
    return CommercialSynthesis.model_validate({
        "commercial_opportunity": opportunity.model_dump(mode="python"),
        "agents": [item.model_dump(mode="python") for item in execution.agents],
        "action_package": execution.action_package.model_dump(mode="python"),
    })


def _unavailable_commercial() -> CommercialSynthesis:
    """Compatibility payload for a profile whose commercial reasoning did not finish.

    SiteAnalysis currently requires a numeric score and at least three agent records.
    They are explicit unavailable-state placeholders, not recommendations. The
    authoritative state is research_status.commercial_score_available=false.
    """
    placeholder_agents = [
        {
            "name": "AI-рекомендация не рассчитана",
            "purpose": "Коммерческий reasoning не завершён",
            "benefit": "Подтверждённая рекомендация отсутствует",
            "priority": "Низкий",
        }
        for _ in range(3)
    ]
    return CommercialSynthesis.model_construct(
        commercial_opportunity=CommercialOpportunity(
            opportunity_type="Коммерческая оценка не рассчитана",
            problem_hypothesis="Коммерческий reasoning не завершён; извлечённые факты сохранены.",
            recommended_solution="Повторить коммерческий synthesis по сохранённому консолидированному профилю.",
            expected_value="Не рассчитана",
            score=0,
            qualification="Недостаточно данных",
        ),
        agents=placeholder_agents,
        action_package=ActionPackage(
            decision_maker_hypothesis="Не рассчитано",
            contact_reason="Не рассчитано",
            demo_scenario=[],
            first_message="Не рассчитано",
            next_action="Повторить коммерческий reasoning без повторного поиска и extraction.",
        ),
    )


def _merge_km_results(requests: list[tuple[tuple[str, ...], BaseModel]]) -> BusinessMachineSynthesis:
    cells: list[BusinessMachineCell] = []
    seen: set[str] = set()
    for allowed_codes, result in requests:
        allowed = set(allowed_codes)
        for cell in result.business_machine_4x4:
            if cell.code not in allowed or cell.code in seen:
                continue
            seen.add(cell.code)
            cells.append(cell)
    return BusinessMachineSynthesis(business_machine_4x4=cells[:16])


_SOURCE_LEVEL_RANK = {
    "confirmed_fact": 3,
    "corroborated_signal": 2,
    "weak_signal": 1,
    "unverified_mention": 0,
}
_CHILD_SOURCE_ID = re.compile(r"^(?P<parent>.+)-b\d+-\d+$")


def _reasoning_source_authority(external_sources: list[dict]) -> dict[str, str]:
    authority = {"S1": "confirmed_fact"}
    for source in external_sources:
        if source.get("lifecycle_state") != "evidence":
            continue
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        match = _CHILD_SOURCE_ID.match(source_id)
        parent_id = match.group("parent") if match else source_id
        level = str(source.get("evidence_level") or "unverified_mention")
        if level not in _SOURCE_LEVEL_RANK:
            level = "unverified_mention"
        current = authority.get(parent_id, "unverified_mention")
        if _SOURCE_LEVEL_RANK[level] > _SOURCE_LEVEL_RANK[current]:
            authority[parent_id] = level
    return authority


def _full_reasoning_profile(merged) -> FullReasoningProfile:
    return FullReasoningProfile(
        company_name=merged.company_name,
        business_summary=merged.business_summary,
        evidence=merged.evidence,
        company_facts=merged.company_facts,
        economic_signals=merged.economic_signals,
        risks_and_assumptions=merged.risks_and_assumptions,
        coverage=merged.coverage.safe_dict(),
    )


def _dossier_status(dossier: ReasoningDossier) -> dict[str, int]:
    metrics = dossier.safe_metrics()
    return {f"reasoning_dossier_{key}": value for key, value in metrics.items()}


def _km_quadrant_prompt(quadrant: str, codes: tuple[str, ...], dossier_context: str) -> str:
    canon = "; ".join(f"{code} {_KM_LABELS[code]}" for code in codes)
    return f"""Построй только квадрант {quadrant} канонической бизнес-модели AIMETON / КМ
из bounded reasoning dossier ниже. Разрешены только коды: {canon}.
Верни не более четырёх ячеек. Для каждой ячейки укажи finding, status,
confidence, source_ids и sales_relevance. Не создавай факты сверх dossier.
Поля fact_counts_by_field и omitted_fact_counts_by_field показывают полноту проекции:
не интерпретируй omitted как отсутствие фактов в полном evidence ledger.
source_authority_by_id — deterministic provenance-level: confirmed_fact сильнее
corroborated_signal, тот сильнее weak_signal. При конфликте предпочитай более сильный
provenance, а не только заявленный confidence модели.
Если данных в dossier нет, используй status=\"Нет данных\" и не компенсируй пробелы
фантазией. Coverage metadata — только агрегаты полноты, не факты компании.
Пиши кратко. Не включай ячейки других квадрантов.

BOUNDED REASONING DOSSIER:\n{dossier_context}
"""


def _km_cell_prompt(code: str, dossier_context: str) -> str:
    return f"""Построй только одну каноническую ячейку {code} {_KM_LABELS[code]}
бизнес-модели AIMETON / КМ из bounded reasoning dossier ниже.
Разрешён только код {code}. Верни не более одной ячейки. Укажи finding, status,
confidence, source_ids и sales_relevance. Не создавай факты сверх dossier.
Поля fact_counts_by_field и omitted_fact_counts_by_field показывают полноту проекции:
не интерпретируй omitted как отсутствие фактов в полном evidence ledger.
Если данных в dossier нет, используй status=\"Нет данных\" и не компенсируй пробелы
фантазией. Coverage metadata — только агрегаты полноты, не факты компании.
Пиши кратко. Не включай другие коды.

BOUNDED REASONING DOSSIER:\n{dossier_context}
"""


async def analyze_with_routerai_split_v2(url: str, title: str, text: str, external_sources: list[dict] | None = None) -> SiteAnalysis:
    """Coverage-preserving extraction → durable ledger → consolidation → bounded reasoning."""
    external_sources = external_sources or []
    accessed_at = datetime.now(timezone.utc).isoformat()

    raw_merged = await extract_profile_parallel(
        request_json=_request_json,
        strict_request_json=request_json_strict,
        url=url,
        title=title,
        text=text,
        external_sources=external_sources,
        accessed_at=accessed_at,
    )
    persist_merged_evidence_ledger(raw_merged)
    merged, consolidation = consolidate_merged_profile(raw_merged, external_sources=external_sources)
    profile = _full_reasoning_profile(merged)
    dossier = build_reasoning_dossier(
        profile,
        source_authority_by_id=_reasoning_source_authority(external_sources),
    )
    dossier_status = _dossier_status(dossier)
    try:
        if current_research() and current_research().stop_requested:
            raise ResearchStopped("research_stopped_by_user")
        result = await asyncio.wait_for(
            _reason_and_assemble(url, title, text, external_sources, profile, dossier, accessed_at),
            timeout=None if deep_research_enabled() or active_settings() else 30.0,
        )
        result.research_status.update({
            "profile_consolidation": consolidation.safe_dict(),
            "commercial_reasoning_state": "succeeded",
            "commercial_score_available": True,
            **dossier_status,
        })
        return result
    except Exception as exc:
        result = _assemble_site_analysis(
            url=url, title=title, text=text, external_sources=external_sources,
            profile=profile, km=BusinessMachineSynthesis(),
            commercial=_unavailable_commercial(), accessed_at=accessed_at,
        )
        stopped = isinstance(exc, ResearchStopped)
        result.readiness.provider_states["routerai"] = (
            "reasoning_stopped_extraction_preserved" if stopped else "reasoning_failed_extraction_preserved"
        )
        result.readiness.analysis_state = "preliminary_hypothesis"
        result.readiness.commercial_priority = 0
        if "commercial_reasoning_incomplete" not in result.readiness.release_blockers:
            result.readiness.release_blockers.append("commercial_reasoning_incomplete")
        result.research_status.update({
            "profile_consolidation": consolidation.safe_dict(),
            "commercial_reasoning_state": "stopped" if stopped else "failed",
            "commercial_score_available": False,
            "commercial_reasoning_error_type": type(exc).__name__,
            **dossier_status,
        })
        result.risks_and_assumptions.append(
            f"Факты извлечены и консолидированы; коммерческий synthesis не завершён ({type(exc).__name__}). "
            "Числовая коммерческая оценка недоступна."
        )
        return result


async def _reason_and_assemble(url, title, text, external_sources, profile, dossier, accessed_at):
    dossier_context = json.dumps(
        dossier.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )

    opportunity_prompt = f"""Ты — AI-продажник AIMETON. На основе только bounded
reasoning dossier выбери одну наиболее доказанную коммерческую AI-возможность.
Сначала учитывай подтверждённые экономические сигналы и пробелы. Определи проблему,
реалистичное AIMETON-решение, ожидаемую ценность, score и qualification. Не формируй
агентов, demo или текст первого контакта на этом этапе. Оценка 80+ допустима только
при прямом подтверждении проблемы, масштаба и реалистичного пилота. Не обещай
неподтверждённый эффект. omitted counters означают только то, что повторяющиеся
низкоприоритетные факты остались в полном ledger вне reasoning context.

BOUNDED REASONING DOSSIER:\n{dossier_context}
"""

    km_specs: list[tuple[str, tuple[str, ...], type[BaseModel], str, int]] = []
    for quadrant, codes in _KM_QUADRANTS:
        if quadrant == "II":
            for code in codes:
                km_specs.append((
                    f"km_reasoning_{code.replace('-', '_')}",
                    (code,), CompactBusinessMachineCell,
                    _km_cell_prompt(code, dossier_context), 700,
                ))
        else:
            km_specs.append((
                f"km_reasoning_{quadrant}",
                codes, CompactBusinessMachineQuadrant,
                _km_quadrant_prompt(quadrant, codes, dossier_context), 1200,
            ))

    km_tasks = [
        request_json_strict(
            phase,
            model_type,
            system=(
                "Возвращай только компактный валидный JSON по схеме. "
                f"Используй только канонические коды: {', '.join(codes)}."
            ),
            prompt=prompt,
            max_tokens=max_tokens,
            timeout_seconds=18.0,
            reasoning_effort="high",
        )
        for phase, codes, model_type, prompt, max_tokens in km_specs
    ]
    gathered = await asyncio.gather(
        *km_tasks,
        request_json_strict(
            "commercial_opportunity_reasoning",
            CompactCommercialOpportunity,
            system="Возвращай только валидный JSON по схеме. Выбери одну наиболее доказанную коммерческую возможность.",
            prompt=opportunity_prompt,
            max_tokens=1500,
            timeout_seconds=25.0,
            reasoning_effort="high",
        ),
        return_exceptions=True,
    )
    for outcome in gathered:
        if isinstance(outcome, BaseException):
            raise outcome
    km_result = _merge_km_results([
        (codes, result)
        for (_, codes, _, _, _), result in zip(km_specs, gathered[:-1], strict=True)
    ])
    opportunity_result = gathered[-1]

    opportunity_context = json.dumps(
        opportunity_result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    execution_prompt = f"""Собери компактный пакет исполнения для уже выбранной
коммерческой возможности. Не переоценивай и не заменяй выбранную возможность.
Верни 3–5 конкретных AI-агентов/инструментов и пакет первого контакта. Каждый пункт
должен быть совместим с bounded dossier; ничего не выдумывай. Это структурирование
уже принятого решения, глубокое рассуждение не требуется.

BOUNDED REASONING DOSSIER:\n{dossier_context}

SELECTED OPPORTUNITY:\n{opportunity_context}
"""
    execution_result = await request_json_strict(
        "commercial_execution",
        CompactCommercialExecution,
        system="Возвращай только компактный валидный JSON по схеме для исполнения выбранной возможности.",
        prompt=execution_prompt,
        max_tokens=1000,
        timeout_seconds=15.0,
        reasoning_enabled=False,
    )

    return _assemble_site_analysis(
        url=url,
        title=title,
        text=text,
        external_sources=external_sources,
        profile=profile,
        km=km_result,
        commercial=_expand_commercial(opportunity_result, execution_result),
        accessed_at=accessed_at,
    )
