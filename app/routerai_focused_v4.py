from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import CompanyFact, EconomicSignal, SiteAnalysis
from app.profile_consolidation import validate_llm_merged_profile
from app.research_control import ResearchStopped, current_research
from app.routerai_compiled_v3 import (
    CompiledSynthesisResponse,
    _synthesis_prompt,
    compile_company_context,
)
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
from app.compiled_context_ledger import persist_compiled_context


class FocusedPassBase(BaseModel):
    focus: str = Field(default="", max_length=80)
    summary: str = Field(default="", max_length=520)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=8)


class IdentityFocusedFact(CompactCompanyFact):
    field: Literal[
        "legal_name", "brand_name", "inn", "ogrn", "registration_status",
        "address", "phones", "emails", "website", "social_accounts", "geography",
        "founders", "executives", "beneficial_owners", "affiliates",
    ]


class IdentityFocusedSlice(FocusedPassBase):
    focus: Literal["identity_governance"] = "identity_governance"
    company_facts: list[IdentityFocusedFact] = Field(default_factory=list, max_length=24)


class OfferingsFocusedFact(CompactCompanyFact):
    field: Literal["products", "customers", "suppliers", "geography", "other"]


class OfferingsFocusedSlice(FocusedPassBase):
    focus: Literal["offerings_customer_operations"] = "offerings_customer_operations"
    company_facts: list[OfferingsFocusedFact] = Field(default_factory=list, max_length=32)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=4)


class EconomicsFocusedFact(CompactCompanyFact):
    field: Literal["headcount", "revenue", "profit", "assets", "taxes", "other"]


class EconomicsFocusedSlice(FocusedPassBase):
    focus: Literal["economics_workforce_technology"] = "economics_workforce_technology"
    company_facts: list[EconomicsFocusedFact] = Field(default_factory=list, max_length=28)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=8)


class SignalsFocusedFact(CompactCompanyFact):
    field: Literal["other"]


class SignalsFocusedSlice(FocusedPassBase):
    focus: Literal["signals_risks_change"] = "signals_risks_change"
    company_facts: list[SignalsFocusedFact] = Field(default_factory=list, max_length=8)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=16)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=10)


class FocusedMergedProfileResponse(BaseModel):
    company_name: str = Field(max_length=180)
    business_summary: str = Field(max_length=700)
    evidence: list[str] = Field(default_factory=list, max_length=12)
    company_facts: list[CompactCompanyFact] = Field(default_factory=list, max_length=100)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=30)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=20)


_FOCUS_PASSES: tuple[tuple[str, type[FocusedPassBase], str], ...] = (
    (
        "identity_governance",
        IdentityFocusedSlice,
        """Сфокусируйся только на идентичности, юридическом статусе, контактах, географии,
людях и связях управления/владения. Ищи legal_name, brand_name, inn, ogrn,
registration_status, address, phones, emails, website, social_accounts, geography,
founders, executives, beneficial_owners, affiliates.
Не превращай учредителя, директора, врача или контактное лицо автоматически в
beneficial_owner. Для relationship-фактов нужен прямой source_id.
Не извлекай каталог услуг, цены и коммерческие рекомендации.""",
    ),
    (
        "offerings_customer_operations",
        OfferingsFocusedSlice,
        """Сфокусируйся на том, что компания реально продаёт/оказывает, кому и как.
Ищи products, customers, suppliers и конкретные проверяемые operational facts в other.
Сжимай каталог семантически: одна бизнес-различимая услуга/продукт = один канонический
факт, даже если она повторяется в меню, прайсе, акции или на нескольких страницах.
Группируй ценовые/тарифные/процедурные варианты, если для бизнеса это одна услуга;
не объединяй действительно разные продуктовые линии. Не считай использование бренда,
оборудования, ПО или технологии доказательством supplier/customer relation.
Не делай коммерческое предложение.""",
    ),
    (
        "economics_workforce_technology",
        EconomicsFocusedSlice,
        """Сфокусируйся на экономике, масштабе, персонале, инфраструктуре и технологиях.
Ищи headcount, revenue, profit, assets, taxes и другие конкретные факты масштаба,
процессов, оборудования, цифровых каналов, автоматизации, вакансий и операционных
ограничений. Для финансов обязательно сохраняй период. Если отдельного поля нет,
используй other только для конкретного проверяемого бизнес-факта, а не рекламного
слогана. Не повторяй полный каталог услуг.""",
    ),
    (
        "signals_risks_change",
        SignalsFocusedSlice,
        """Сфокусируйся на значимых сигналах и изменениях: рост/сжатие, вакансии,
юридические события, отзывы, акции, цифровые точки контакта, операционные разрывы,
признаки ручных процессов, технологические зависимости, подтверждённые риски.
Основной результат этого прохода — economic_signals и risks_and_assumptions.
Факты добавляй только если они нужны для понимания сигнала и прямо подтверждаются
источником. Не превращай сигнал в готовую коммерческую гипотезу.""",
    ),
)


def _focus_prompt(*, focus: str, instructions: str, context: str) -> str:
    return f"""Ты Focused Evidence Analyst AIMETON.

Один и тот же подготовленный evidence context анализируется несколькими независимыми
проходами с разными фокусами. Твоя задача — не построить весь профиль, а сжать сырец
в значимые факты только для указанного фокуса.

ОБЩИЕ ПРАВИЛА
- Используй весь переданный context совместно, а не отдельные документы изолированно.
- Не создавай факты, роли, цифры или source_ids, которых нет во входе.
- Возвращай только независимые значимые факты; повторы одной сущности сливай.
- Каждый fact/signal обязан ссылаться на реально поддерживающие source_ids.
- Цена, акция, страница, меню или иная формулировка сами по себе не создают новую услугу.
- Неизвестное не заполняй догадкой.
- Не делай финальный commercial synthesis.
- Пиши компактно: это предварительный слой сжатия, который затем объединит отдельный LLM.
- Не пытайся заполнить максимально допустимое число элементов. Выбирай только наиболее
  значимые и репрезентативные факты своего фокуса: лучше 12 сильных фактов, чем 30 слабых.

FOCUS: {focus}
{instructions}

PREPARED EVIDENCE CONTEXT:
{context}
"""


def _merge_prompt(focused_results: list[FocusedPassBase]) -> str:
    payload = [
        item.model_dump(mode="json")
        for item in focused_results
    ]
    return f"""Ты Profile Merge Analyst AIMETON.

Ниже результаты нескольких независимых LLM-проходов по ОДНОМУ И ТОМУ ЖЕ evidence
context, каждый с отдельным фокусом. Собери из них единый нормализованный CompanyProfile.

Требования:
- дедуплицируй одинаковые факты семантически, а не только по строковому совпадению;
- при конфликте не выбирай удобный вариант: сохрани разные подтверждённые значения
  отдельно и отметь конфликт/ограничение в risks_and_assumptions;
- source_ids сохраняй только если они реально были приведены в focused outputs;
- products — канонические уникальные услуги/товары, без размножения по цене/акции/странице;
- founders/executives/beneficial_owners/affiliates/customers/suppliers оставляй только
  при прямом подтверждении соответствующей роли;
- financial facts разделяй по показателю и периоду;
- economic_signals не дублируй как company_facts без необходимости;
- company_name и business_summary сформируй по совокупности проходов;
- не добавляй новые факты из собственных знаний;
- итог должен быть компактным и пригодным для одного последующего reasoning pass.

FOCUSED PASSES:
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
"""


async def _run_focused_pass(
    *,
    focus: str,
    model_type: type[FocusedPassBase],
    instructions: str,
    context: str,
) -> FocusedPassBase:
    return await request_json_strict(
        f"profile_focus_{focus}",
        model_type,
        system=(
            "Возвращай только валидный компактный JSON по схеме. "
            "Это один тематический проход по общему evidence context."
        ),
        prompt=_focus_prompt(
            focus=focus,
            instructions=instructions,
            context=context,
        ),
        max_tokens=5_000,
        timeout_seconds=120.0,
        reasoning_enabled=False,
    )


async def analyze_with_routerai_focused_v4(
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]] | None = None,
) -> SiteAnalysis:
    """Prepared context -> 4 focused passes -> LLM merge -> unified synthesis."""
    external_sources = external_sources or []
    control = current_research()
    if control and control.stop_requested:
        raise ResearchStopped("research_stopped_by_user")

    context, context_stats = compile_company_context(
        url=url,
        title=title,
        text=text,
        external_sources=external_sources,
    )
    compiled_context_record_id = persist_compiled_context(
        context,
        context_stats.safe_dict(),
    )

    tasks = [
        _run_focused_pass(
            focus=focus,
            model_type=model_type,
            instructions=instructions,
            context=context,
        )
        for focus, model_type, instructions in _FOCUS_PASSES
    ]
    focus_outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    focused_results: list[FocusedPassBase] = []
    focus_failures: list[str] = []
    for (focus, _, _), outcome in zip(_FOCUS_PASSES, focus_outcomes):
        if isinstance(outcome, Exception):
            error_type = getattr(outcome, "error_type", None) or type(outcome).__name__
            focus_failures.append(f"{focus}:{error_type}")
            continue
        focused_results.append(outcome)

    if len(focused_results) < 2:
        raise RuntimeError(
            "focused_profile_insufficient:"
            + ",".join(focus_failures)
        )

    if control and control.stop_requested:
        raise ResearchStopped("research_stopped_by_user")

    merged_llm = await request_json_strict(
        "profile_focused_merge",
        FocusedMergedProfileResponse,
        system=(
            "Возвращай только валидный компактный JSON по схеме. "
            "Семантически объедини focused outputs в единый профиль."
        ),
        prompt=_merge_prompt(focused_results),
        max_tokens=6_500,
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
        extraction_units_total=len(_FOCUS_PASSES),
        extraction_units_processed=len(focused_results),
        complete=len(focused_results) == len(_FOCUS_PASSES),
    )
    raw = MergedProfileExtraction(
        company_name=merged_llm.company_name or title,
        business_summary=merged_llm.business_summary,
        evidence=list(merged_llm.evidence),
        company_facts=[
            CompanyFact.model_validate(item.model_dump(mode="python"))
            for item in merged_llm.company_facts
        ],
        economic_signals=[
            EconomicSignal.model_validate(item.model_dump(mode="python"))
            for item in merged_llm.economic_signals
        ],
        risks_and_assumptions=[
            *merged_llm.risks_and_assumptions,
            *(
                [f"Focused extraction degraded: {', '.join(focus_failures)}"]
                if focus_failures
                else []
            ),
        ],
        coverage=coverage,
    )
    persist_merged_evidence_ledger(raw)

    # Deterministic code below is validation/normalization only, not semantic extraction.
    merged, consolidation = validate_llm_merged_profile(
        raw,
        external_sources=external_sources,
    )
    profile = _full_reasoning_profile(merged)
    profile_context = json.dumps(
        {
            "company_name": profile.company_name,
            "business_summary": profile.business_summary,
            "company_facts": [
                item.model_dump(mode="json")
                for item in profile.company_facts
            ],
            "economic_signals": [
                item.model_dump(mode="json")
                for item in profile.economic_signals
            ],
            "risks_and_assumptions": profile.risks_and_assumptions,
            "coverage": profile.coverage,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        if control and control.stop_requested:
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
        result.readiness.provider_states["routerai"] = (
            "reasoning_failed_extraction_preserved"
        )
        result.readiness.analysis_state = "preliminary_hypothesis"
        if "commercial_reasoning_incomplete" not in result.readiness.release_blockers:
            result.readiness.release_blockers.append(
                "commercial_reasoning_incomplete"
            )
        result.risks_and_assumptions.append(
            f"Focused synthesis не завершён ({type(exc).__name__}); "
            "нормализованный профиль и evidence сохранены."
        )
        synthesis_state = "failed"
        synthesis_calls = 1

    result.research_status.update({
        "analysis_orchestration": "focused_v4_multipass",
        "compiled_context": context_stats.safe_dict(),
        "compiled_context_record_id": compiled_context_record_id or "",
        "focused_profile_passes_requested": len(_FOCUS_PASSES),
        "focused_profile_passes_succeeded": len(focused_results),
        "focused_profile_passes_failed": len(focus_failures),
        "focused_profile_failures": ",".join(focus_failures),
        "profile_consolidation": consolidation.safe_dict(),
        "extraction_coverage": profile.coverage,
        "core_llm_focus_calls": len(_FOCUS_PASSES),
        "core_llm_merge_calls": 1,
        "core_llm_synthesis_calls": synthesis_calls,
        "core_llm_calls": len(_FOCUS_PASSES) + 1 + synthesis_calls,
        "commercial_reasoning_state": synthesis_state,
        "commercial_score_available": synthesis_state == "succeeded",
    })
    return result
