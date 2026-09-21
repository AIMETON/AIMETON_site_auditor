from __future__ import annotations

import pytest

import app.routerai_focused_v4 as focused
from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
)
from app.research_control import ResearchControl, bind_research
from app.routerai_profile_extraction import CompactCompanyFact, CompactEconomicSignal


@pytest.mark.asyncio
async def test_focused_v4_uses_same_context_for_four_passes_then_direct_synthesis(monkeypatch):
    phases: list[str] = []
    focus_contexts: list[str] = []

    async def fake_request(phase, model_type, **kwargs):
        phases.append(phase)
        prompt = kwargs["prompt"]
        if phase.startswith("profile_focus_"):
            marker = "PREPARED EVIDENCE CONTEXT:\n"
            focus_contexts.append(prompt.split(marker, 1)[1])
            focus_name = phase.removeprefix("profile_focus_")
            if focus_name == "identity_governance":
                return model_type(
                    summary=focus_name,
                    company_facts=[{
                        "field": "legal_name",
                        "value": 'ООО "ЭКЗАМПЛ"',
                        "confidence": "Высокая",
                        "source_ids": ["S1"],
                    }],
                )
            if focus_name == "offerings_customer_operations":
                return model_type(
                    summary=focus_name,
                    company_facts=[{
                        "field": "products",
                        "value": "Имплантация",
                        "confidence": "Высокая",
                        "source_ids": ["S1"],
                    }],
                )
            if focus_name == "economics_workforce_technology":
                return model_type(
                    summary=focus_name,
                    company_facts=[{
                        "field": "other",
                        "value": "Доступна онлайн-запись",
                        "confidence": "Высокая",
                        "source_ids": ["S1"],
                    }],
                )
            return model_type(
                summary=focus_name,
                economic_signals=[{
                    "signal": "Цифровой канал заявок",
                    "evidence": "На сайте доступна онлайн-запись",
                    "business_effect": "Часть входящего спроса проходит через сайт",
                    "confidence": "Высокая",
                    "source_ids": ["S1"],
                }],
            )

        if phase == "compiled_business_commercial_synthesis":
            return focused.CompiledSynthesisResponse(
                business_machine_4x4=[
                    BusinessMachineCell(
                        code="I-I",
                        detail_operator="I — Коммуникационные системы",
                        vertex="Взаимодействие",
                        finding="Есть онлайн-запись",
                        status="Подтверждено",
                        confidence="Высокая",
                        source_ids=["S1"],
                        sales_relevance="Цифровой входящий канал",
                    )
                ],
                commercial_opportunity=CommercialOpportunity(
                    opportunity_type="AI-квалификация заявок",
                    problem_hypothesis="Компания принимает заявки через сайт",
                    recommended_solution="AI-квалификатор",
                    expected_value="Снижение ручной первичной обработки",
                    score=65,
                    qualification="Перспективная",
                    source_ids=["S1"],
                ),
                agents=[
                    AgentRecommendation(name="A1", purpose="Квалификация", benefit="Скорость"),
                    AgentRecommendation(name="A2", purpose="Маршрутизация", benefit="Распределение"),
                    AgentRecommendation(name="A3", purpose="Контроль", benefit="Наблюдаемость"),
                ],
                action_package=ActionPackage(
                    decision_maker_hypothesis="Руководитель",
                    contact_reason="Показать пилот",
                    demo_scenario=["Новая заявка", "Квалификация"],
                    first_message="Предлагаем пилот.",
                    next_action="Демо",
                ),
            )
        raise AssertionError(phase)

    monkeypatch.setattr(focused, "request_json_strict", fake_request)
    monkeypatch.setattr(focused, "persist_compiled_context", lambda *args, **kwargs: "CTX1")
    monkeypatch.setattr(focused, "persist_merged_evidence_ledger", lambda profile: None)

    result = await focused.analyze_with_routerai_focused_v4(
        "https://example.org/",
        "Example",
        "Официальный сайт. Доступна онлайн-запись. Имплантация.",
        [],
    )

    assert phases == [
        "profile_focus_identity_governance",
        "profile_focus_offerings_customer_operations",
        "profile_focus_economics_workforce_technology",
        "profile_focus_signals_risks_change",
        "compiled_business_commercial_synthesis",
    ]
    assert len(focus_contexts) == 4
    assert len(set(focus_contexts)) == 1
    assert result.research_status["analysis_orchestration"] == "focused_v4_multipass"
    assert result.research_status["core_llm_focus_calls"] == 4
    assert result.research_status["core_llm_reconcile_calls"] == 0
    assert result.research_status["core_llm_merge_calls"] == 0
    assert result.research_status["core_llm_synthesis_calls"] == 1
    assert result.research_status["core_llm_calls"] == 5
    assert result.research_status["focused_profile_passes_succeeded"] == 4
    assert result.company_name == 'ООО "ЭКЗАМПЛ"'
    assert any(fact.field == "products" for fact in result.company_facts)


@pytest.mark.asyncio
async def test_deep_runtime_prefers_focused_v4_and_can_roll_back(monkeypatch):
    from app import routerai_runtime as runtime
    from app.heuristics import heuristic_analysis

    calls: list[str] = []

    async def focused_call(url, title, text, sources):
        calls.append("focused")
        return heuristic_analysis(url, title, text)

    async def compiled_call(url, title, text, sources):
        calls.append("compiled")
        return heuristic_analysis(url, title, text)

    monkeypatch.setattr(runtime, "analyze_with_routerai_focused_v4", focused_call)
    monkeypatch.setattr(runtime, "analyze_with_routerai_compiled_v3", compiled_call)

    monkeypatch.setenv("AIMETON_FOCUSED_MULTIPASS", "1")
    monkeypatch.setenv("AIMETON_COMPILED_TWO_CALL", "1")
    with bind_research(ResearchControl(deep=True)):
        await runtime.run_bounded_routerai_analysis(
            "https://example.org/", "Example", "text", []
        )
    assert calls == ["focused"]

    calls.clear()
    monkeypatch.setenv("AIMETON_FOCUSED_MULTIPASS", "0")
    with bind_research(ResearchControl(deep=True)):
        await runtime.run_bounded_routerai_analysis(
            "https://example.org/", "Example", "text", []
        )
    assert calls == ["compiled"]


def test_focused_schemas_assign_each_fact_field_to_one_owner() -> None:
    schema_types = [
        focused.IdentityFocusedSlice,
        focused.OfferingsFocusedSlice,
        focused.EconomicsFocusedSlice,
        focused.SignalsFocusedSlice,
    ]
    owners: dict[str, list[str]] = {}
    for schema_type in schema_types:
        schema = schema_type.model_json_schema()
        defs = schema.get("$defs", {})
        for name, definition in defs.items():
            field_schema = (definition.get("properties") or {}).get("field") or {}
            values = field_schema.get("enum")
            if values is None and "const" in field_schema:
                values = [field_schema["const"]]
            for value in values or []:
                owners.setdefault(str(value), []).append(schema_type.__name__)

    assert all(len(schema_owners) == 1 for schema_owners in owners.values())
    assert "products" in owners
    assert "legal_name" in owners
    assert "revenue" in owners


def test_focused_schemas_are_semantic_shortlists() -> None:
    caps = {
        focused.IdentityFocusedSlice: 14,
        focused.OfferingsFocusedSlice: 14,
        focused.EconomicsFocusedSlice: 12,
    }
    for schema_type, expected_max in caps.items():
        schema = schema_type.model_json_schema()
        facts = (schema.get("properties") or {}).get("company_facts") or {}
        assert facts.get("maxItems") == expected_max

    signal_schema = focused.SignalsFocusedSlice.model_json_schema()
    signals = (signal_schema.get("properties") or {}).get("economic_signals") or {}
    assert signals.get("maxItems") == 10


def test_signal_focus_accepts_longer_text_and_normalizes_confidence() -> None:
    raw = focused.FocusedEconomicSignal(
        signal="Сигнал " + "x" * 120,
        evidence="Доказательство " + "y" * 220,
        business_effect="Эффект " + "z" * 220,
        confidence="high",
        source_ids=["S1", "S2", "S3", "S4"],
    )

    normalized = focused._normalized_signal(raw)

    assert normalized.confidence == "Средняя"
    assert normalized.source_ids == ["S1", "S2", "S3", "S4"]


def test_focused_business_summary_truncates_on_word_boundary() -> None:
    results = [
        focused.IdentityFocusedSlice(summary="A" * 320),
        focused.OfferingsFocusedSlice(summary="B" * 320),
        focused.EconomicsFocusedSlice(summary="C" * 320),
    ]

    summary = focused._focused_business_summary(results)

    assert len(summary) <= 701
    assert summary.endswith("…")
