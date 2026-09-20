from __future__ import annotations

import json

import pytest

import app.routerai_compiled_v3 as compiled
from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
)
from app.routerai_profile_extraction import CompactCompanyFact, CompactEconomicSignal
from app.research_control import ResearchControl, bind_research


def test_context_compiler_groups_model_records_by_document_id() -> None:
    context, stats = compiled.compile_company_context(
        url="https://example.org/",
        title="Example",
        text="Official root text",
        external_sources=[
            {
                "id": "D1",
                "url": "https://example.org/services",
                "query_kind": "other",
                "source_class": "official",
                "evidence_level": "confirmed_fact",
                "snippet": "Имплантация",
            },
            {
                "id": "D1",
                "url": "https://example.org/services",
                "query_kind": "contact",
                "source_class": "official",
                "evidence_level": "confirmed_fact",
                "snippet": "Телефон +7 000 000-00-00",
            },
            {
                "id": "R1",
                "url": "https://registry.example/company",
                "query_kind": "registry",
                "source_class": "registry",
                "evidence_level": "corroborated_signal",
                "snippet": "ИНН 1234567890",
            },
        ],
    )

    payload = json.loads(context)
    assert stats.input_records == 3
    assert stats.document_groups == 2
    assert [item["id"] for item in payload["documents"]] == ["D1", "R1"]
    assert payload["documents"][0]["query_kinds"] == ["contact", "other"]
    assert "Имплантация" in payload["documents"][0]["text"]
    assert "Телефон" in payload["documents"][0]["text"]


@pytest.mark.asyncio
async def test_compiled_analysis_uses_exactly_two_core_llm_calls(monkeypatch) -> None:
    phases: list[str] = []

    async def fake_request(phase, model_type, **kwargs):
        phases.append(phase)
        if model_type is compiled.CompiledProfileResponse:
            return compiled.CompiledProfileResponse(
                company_name="Example",
                business_summary="Сервисная компания",
                evidence=["Есть официальный сайт"],
                company_facts=[
                    CompactCompanyFact(
                        field="website",
                        value="https://example.org/",
                        confidence="Высокая",
                        source_ids=["S1"],
                    ),
                    CompactCompanyFact(
                        field="products",
                        value="Имплантация",
                        confidence="Высокая",
                        source_ids=["S1"],
                    ),
                ],
                economic_signals=[
                    CompactEconomicSignal(
                        signal="Онлайн-запись",
                        evidence="На сайте доступна онлайн-запись",
                        business_effect="Есть цифровой канал заявок",
                        confidence="Высокая",
                        source_ids=["S1"],
                    )
                ],
            )
        if model_type is compiled.CompiledSynthesisResponse:
            return compiled.CompiledSynthesisResponse(
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
                    problem_hypothesis="Компания использует онлайн-запись",
                    recommended_solution="AI-квалификатор",
                    expected_value="Снижение ручной обработки",
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
        raise AssertionError(model_type)

    monkeypatch.setattr(compiled, "request_json_strict", fake_request)
    monkeypatch.setattr(compiled, "persist_merged_evidence_ledger", lambda profile: None)

    result = await compiled.analyze_with_routerai_compiled_v3(
        "https://example.org/",
        "Example",
        "Official root text with online booking",
        [],
    )

    assert phases == [
        "profile_compiled_extraction",
        "compiled_business_commercial_synthesis",
    ]
    assert result.research_status["analysis_orchestration"] == "compiled_v3_two_call"
    assert result.research_status["core_llm_calls"] == 2
    assert result.research_status["extraction_coverage"]["extraction_units_total"] == 1
    assert any(fact.field == "products" for fact in result.company_facts)


@pytest.mark.asyncio
async def test_deep_runtime_uses_compiled_path(monkeypatch) -> None:
    from app import routerai_runtime as runtime
    from app.heuristics import heuristic_analysis

    monkeypatch.setenv("AIMETON_FOCUSED_MULTIPASS", "0")
    monkeypatch.delenv("AIMETON_COMPILED_TWO_CALL", raising=False)
    calls = []

    async def compiled_call(url, title, text, sources):
        calls.append("compiled")
        return heuristic_analysis(url, title, text)

    async def other_call(*args, **kwargs):
        calls.append("other")
        return heuristic_analysis("https://example.org/", "Example", "text")

    monkeypatch.setattr(runtime, "analyze_with_routerai_compiled_v3", compiled_call)
    monkeypatch.setattr(runtime, "analyze_with_routerai_split_v2", other_call)
    monkeypatch.setattr(runtime, "analyze_with_routerai", other_call)

    with bind_research(ResearchControl(deep=True)):
        await runtime.run_bounded_routerai_analysis(
            "https://example.org/", "Example", "text", []
        )

    assert calls == ["compiled"]
