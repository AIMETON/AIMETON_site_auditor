from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.models import CompanyFact, EconomicSignal, SiteAnalysis
from app.llm_runtime_settings import LlmRole, resolve_llm_runtime
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
    summary: str = Field(default="", max_length=700)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=5)


class IdentityFocusedFact(CompactCompanyFact):
    field: Literal[
        "legal_name", "brand_name", "inn", "ogrn", "registration_status",
        "address", "phones", "emails", "website", "social_accounts", "geography",
        "founders", "executives", "beneficial_owners", "affiliates",
    ]


class IdentityFocusedSlice(FocusedPassBase):
    focus: Literal["identity_governance"] = "identity_governance"
    company_facts: list[IdentityFocusedFact] = Field(default_factory=list, max_length=14)


class OfferingsFocusedFact(CompactCompanyFact):
    field: Literal["products", "customers", "suppliers"]


class OfferingsFocusedSlice(FocusedPassBase):
    focus: Literal["offerings_customer_operations"] = "offerings_customer_operations"
    company_facts: list[OfferingsFocusedFact] = Field(default_factory=list, max_length=14)
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=3)


class EconomicsFocusedFact(CompactCompanyFact):
    field: Literal["headcount", "revenue", "profit", "assets", "taxes", "other"]


class EconomicsFocusedSlice(FocusedPassBase):
    focus: Literal["economics_workforce_technology"] = "economics_workforce_technology"
    company_facts: list[EconomicsFocusedFact] = Field(default_factory=list, max_length=12)
    # Transport buffer: the semantic contract remains five signals, but tolerate
    # small provider over-production and trim deterministically after validation.
    economic_signals: list[CompactEconomicSignal] = Field(default_factory=list, max_length=10)


class FocusedEconomicSignal(BaseModel):
    signal: str = Field(max_length=240)
    evidence: str = Field(max_length=360)
    business_effect: str = Field(max_length=360)
    confidence: str = Field(default="Средняя", max_length=32)
    source_ids: list[str] = Field(default_factory=list, max_length=5)


class SignalsFocusedSlice(FocusedPassBase):
    focus: Literal["signals_risks_change"] = "signals_risks_change"
    economic_signals: list[FocusedEconomicSignal] = Field(default_factory=list, max_length=10)
    risks_and_assumptions: list[str] = Field(default_factory=list, max_length=6)


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
Ищи products, customers, suppliers.
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
слогана. Верни не более 5 economic_signals, по убыванию бизнес-значимости.
Не повторяй полный каталог услуг.""",
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
- Это semantic shortlist, а не исчерпывающий реестр. Выбирай только самые значимые,
  репрезентативные и независимые факты своего фокуса.
- Нормальный объём — 5–10 элементов. Не заполняй массив до максимума только потому,
  что схема это разрешает. Если 6 фактов описывают бизнес лучше, верни 6.
- Для products группируй конкретные процедуры/тарифные варианты в бизнес-различимые
  направления услуг; детали прайса остаются в raw evidence и не обязаны попадать сюда.

FOCUS: {focus}
{instructions}

PREPARED EVIDENCE CONTEXT:
{context}
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
        max_tokens=4_000,
        timeout_seconds=120.0,
        reasoning_enabled=False,
    )


def _normalized_signal(item: BaseModel) -> EconomicSignal:
    data = item.model_dump(mode="python")
    confidence = str(data.get("confidence") or "").strip()
    if confidence not in {"Высокая", "Средняя", "Низкая"}:
        confidence = "Средняя"
    return EconomicSignal(
        signal=str(data.get("signal") or "").strip(),
        evidence=str(data.get("evidence") or "").strip(),
        business_effect=str(data.get("business_effect") or "").strip(),
        confidence=confidence,
        source_ids=[str(value).strip() for value in data.get("source_ids") or [] if str(value).strip()],
    )


def _bounded_focused_signal_items(result: FocusedPassBase) -> list[BaseModel]:
    items = list(getattr(result, "economic_signals", []))
    caps = {
        "offerings_customer_operations": 3,
        "economics_workforce_technology": 5,
        "signals_risks_change": 10,
    }
    cap = caps.get(str(getattr(result, "focus", "")), len(items))
    return items[:cap]


def _safe_phase_failure_descriptor(outcome: Exception) -> str:
    """Expose only safe phase/validation metadata, never provider payloads."""
    error_type = getattr(outcome, "error_type", None) or type(outcome).__name__
    cause = getattr(outcome, "__cause__", None)
    if isinstance(cause, ValidationError):
        errors = cause.errors(include_input=False, include_url=False)
        if errors:
            first = errors[0]
            loc = ".".join(str(value) for value in first.get("loc", ())) or "root"
            kind = str(first.get("type") or "validation")
            return f"{error_type}@{loc}:{kind}"
    return str(error_type)


def _focus_failure_descriptor(outcome: Exception) -> str:
    return _safe_phase_failure_descriptor(outcome)


def _focused_profile_name(facts: list[CompanyFact], fallback: str) -> str:
    for field in ("legal_name", "brand_name"):
        for fact in facts:
            if fact.field == field and str(fact.value).strip():
                return str(fact.value).strip()
    return fallback


def _focused_business_summary(results: list[FocusedPassBase]) -> str:
    """Use one LLM-authored overview instead of concatenating per-focus summaries.

    Every focused pass sees the full prepared context, so concatenating their summaries
    repeats the same company description from four angles. Prefer the offerings/operations
    pass because it is the closest universal description of what the company actually does;
    fall back to economics, identity, then signals when that pass is unavailable.
    """
    priority = (
        "offerings_customer_operations",
        "economics_workforce_technology",
        "identity_governance",
        "signals_risks_change",
    )
    by_focus = {str(result.focus): result for result in results}
    ordered = [by_focus[focus] for focus in priority if focus in by_focus]
    ordered.extend(result for result in results if result not in ordered)
    for result in ordered:
        value = " ".join(str(result.summary or "").split()).strip()
        if not value:
            continue
        if len(value) <= 700:
            return value
        clipped = value[:700].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return clipped + "…"
    return ""


async def analyze_with_routerai_focused_v4(
    url: str,
    title: str,
    text: str,
    external_sources: list[dict[str, Any]] | None = None,
) -> SiteAnalysis:
    """Prepared context -> 4 focused LLM passes -> unified synthesis."""
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
            focus_failures.append(f"{focus}:{_focus_failure_descriptor(outcome)}")
            continue
        focused_results.append(outcome)

    if control is not None:
        control.checkpoint(
            "focused_v4/outcomes",
            {
                "requested": len(_FOCUS_PASSES),
                "succeeded": len(focused_results),
                "failed": len(focus_failures),
                "failures": list(focus_failures),
            },
        )

    if len(focused_results) < 2:
        raise RuntimeError(
            "focused_profile_insufficient:"
            + ",".join(focus_failures)
        )

    if control and control.stop_requested:
        raise ResearchStopped("research_stopped_by_user")

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
    focused_facts = [
        CompanyFact.model_validate(item.model_dump(mode="python"))
        for focused_result in focused_results
        for item in getattr(focused_result, "company_facts", [])
    ]
    focused_signals = [
        _normalized_signal(item)
        for focused_result in focused_results
        for item in _bounded_focused_signal_items(focused_result)
    ]
    focused_risks = [
        str(item)
        for focused_result in focused_results
        for item in getattr(focused_result, "risks_and_assumptions", [])
        if str(item).strip()
    ]
    raw = MergedProfileExtraction(
        company_name=_focused_profile_name(focused_facts, title),
        business_summary=_focused_business_summary(focused_results),
        evidence=[],
        company_facts=focused_facts,
        economic_signals=focused_signals,
        risks_and_assumptions=[
            *focused_risks,
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

    synthesis_error_phase = ""
    synthesis_error_descriptor = ""
    extraction_runtime = resolve_llm_runtime(LlmRole.EXTRACTION)
    reasoning_runtime = resolve_llm_runtime(LlmRole.REASONING)
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
        result.readiness.provider_states.pop("routerai", None)
        result.readiness.provider_states[reasoning_runtime.provider] = "active"
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
        result.readiness.provider_states.pop("routerai", None)
        result.readiness.provider_states[reasoning_runtime.provider] = (
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
        synthesis_error_phase = str(getattr(exc, "phase", "") or "")
        synthesis_error_descriptor = _safe_phase_failure_descriptor(exc)

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
        "core_llm_reconcile_calls": 0,
        "core_llm_merge_calls": 0,
        "core_llm_synthesis_calls": synthesis_calls,
        "core_llm_calls": len(_FOCUS_PASSES) + synthesis_calls,
        "commercial_reasoning_state": synthesis_state,
        "commercial_reasoning_error_phase": synthesis_error_phase,
        "commercial_reasoning_failure": synthesis_error_descriptor,
        "commercial_score_available": synthesis_state == "succeeded",
        "llm_extraction_provider": extraction_runtime.provider,
        "llm_extraction_profile": extraction_runtime.profile_name,
        "llm_extraction_model": extraction_runtime.model,
        "llm_reasoning_provider": reasoning_runtime.provider,
        "llm_reasoning_profile": reasoning_runtime.profile_name,
        "llm_reasoning_model": reasoning_runtime.model,
    })
    return result
