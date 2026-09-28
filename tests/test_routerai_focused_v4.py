from __future__ import annotations

import pytest
from pydantic import ValidationError

import app.routerai_focused_v4 as focused
from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
)
from app.research_control import ResearchControl, bind_research
from app.routerai_profile_extraction import CompactCompanyFact, CompactEconomicSignal
from app.routerai_split_synthesis import SplitSynthesisPhaseError


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


def test_focused_schemas_do_not_encode_arbitrary_semantic_size_caps() -> None:
    for schema_type in (
        focused.IdentityFocusedSlice,
        focused.OfferingsFocusedSlice,
        focused.EconomicsFocusedSlice,
        focused.SignalsFocusedSlice,
    ):
        schema = schema_type.model_json_schema()
        properties = schema.get("properties") or {}
        facts = properties.get("company_facts") or {}
        signals = properties.get("economic_signals") or {}
        summary = properties.get("summary") or {}
        assert "maxItems" not in facts
        assert "maxItems" not in signals
        assert "maxLength" not in summary

    identity_schema = focused.IdentityFocusedSlice.model_json_schema()
    identity_fact = identity_schema["$defs"]["IdentityFocusedFact"]
    assert "maxLength" not in identity_fact["properties"]["value"]


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


def test_focused_business_summary_prefers_one_offerings_overview() -> None:
    results = [
        focused.IdentityFocusedSlice(summary="Юридическая идентичность компании.", company_facts=[]),
        focused.OfferingsFocusedSlice(summary="Компания оказывает стоматологические услуги.", company_facts=[]),
        focused.EconomicsFocusedSlice(summary="Компания использует цифровые каналы записи.", company_facts=[]),
        focused.SignalsFocusedSlice(summary="Есть сигналы операционных изменений."),
    ]

    summary = focused._focused_business_summary(results)

    assert summary == "Компания оказывает стоматологические услуги."
    assert "Юридическая идентичность" not in summary
    assert "операционных изменений" not in summary


def test_focused_business_summary_falls_back_when_offerings_pass_missing() -> None:
    results = [
        focused.IdentityFocusedSlice(summary="Идентичность.", company_facts=[]),
        focused.EconomicsFocusedSlice(summary="Экономика и масштаб.", company_facts=[]),
    ]

    assert focused._focused_business_summary(results) == "Экономика и масштаб."


def test_focus_failure_descriptor_exposes_safe_validation_location_only() -> None:
    try:
        focused.IdentityFocusedSlice(
            company_facts=[{
                "field": "products",
                "value": "Имплантация",
                "source_ids": ["S1"],
            }],
        )
    except Exception as cause:
        wrapped = SplitSynthesisPhaseError("profile_focus_identity_governance", "ValidationError")
        wrapped.__cause__ = cause
    else:
        raise AssertionError("expected validation error")

    descriptor = focused._focus_failure_descriptor(wrapped)

    assert descriptor == "ValidationError@company_facts.0.field:literal_error"
    assert "Имплантация" not in descriptor


def test_focused_summary_preserves_semantically_useful_long_text() -> None:
    summary = "Экономика и инфраструктура. " + ("подтверждённый факт " * 80)

    result = focused.EconomicsFocusedSlice(summary=summary, company_facts=[])

    assert result.summary == summary
    assert len(result.summary) > 700


def test_economics_focus_preserves_all_distinct_signals() -> None:
    items = [
        focused.CompactEconomicSignal(
            signal=f"signal-{index}",
            evidence=f"evidence-{index}",
            business_effect=f"effect-{index}",
            confidence="Средняя",
            source_ids=["S1"],
        )
        for index in range(7)
    ]
    result = focused.EconomicsFocusedSlice(
        summary="economics",
        company_facts=[],
        economic_signals=items,
    )

    preserved = focused._focused_signal_items(result)

    assert [item.signal for item in preserved] == [f"signal-{index}" for index in range(7)]


@pytest.mark.asyncio
async def test_focused_synthesis_failure_publishes_safe_phase_diagnostics(monkeypatch) -> None:
    async def fake_request(phase, model_type, **kwargs):
        if phase.startswith("profile_focus_"):
            payload = {"summary": phase.removeprefix("profile_focus_")}
            if "company_facts" in model_type.model_fields:
                payload["company_facts"] = []
            return model_type(**payload)
        if phase == "compiled_business_commercial_synthesis":
            try:
                focused.CompiledSynthesisResponse.model_validate({
                    "business_machine_4x4": [],
                    "commercial_opportunity": {"score": "not-an-int"},
                })
            except Exception as cause:
                wrapped = SplitSynthesisPhaseError(phase, "ValidationError")
                wrapped.__cause__ = cause
                raise wrapped
        raise AssertionError(phase)

    monkeypatch.setattr(focused, "request_json_strict", fake_request)
    monkeypatch.setattr(focused, "persist_compiled_context", lambda *args, **kwargs: "CTX1")
    monkeypatch.setattr(focused, "persist_merged_evidence_ledger", lambda profile: None)

    result = await focused.analyze_with_routerai_focused_v4(
        "https://example.org/",
        "Example",
        "Официальный сайт компании.",
        [],
    )

    assert result.research_status["commercial_reasoning_state"] == "failed"
    assert (
        result.research_status["commercial_reasoning_error_phase"]
        == "compiled_business_commercial_synthesis"
    )
    assert result.research_status["commercial_reasoning_failure"].startswith(
        "ValidationError@"
    )
    assert "not-an-int" not in result.research_status["commercial_reasoning_failure"]



def test_fact_bearing_focus_rejects_silent_schema_drift():
    payload = {
        "focus": "identity_governance",
        "summary": "Компания работает в Красноярске",
        "analysis": "Свободная форма вместо объявленной схемы",
    }
    with pytest.raises(ValidationError):
        focused.IdentityFocusedSlice.model_validate(payload)

    with pytest.raises(ValidationError):
        focused.OfferingsFocusedSlice.model_validate({
            "focus": "offerings_customer_operations",
            "summary": "Есть стоматологические услуги",
        })

    with pytest.raises(ValidationError):
        focused.EconomicsFocusedSlice.model_validate({
            "focus": "economics_workforce_technology",
            "summary": "Используются цифровые каналы",
        })



def test_identity_focus_preserves_long_atomic_values_without_length_cap() -> None:
    value = "г. Красноярск, " + ("значимая адресная деталь; " * 80)

    result = focused.IdentityFocusedSlice(
        summary="Идентичность и контакты",
        company_facts=[{
            "field": "address",
            "value": value,
            "confidence": "Высокая",
            "source_ids": ["S1"],
        }],
    )

    assert result.company_facts[0].value == value
    schema = focused.IdentityFocusedSlice.model_json_schema()
    fact_def = schema["$defs"]["IdentityFocusedFact"]
    assert "maxLength" not in fact_def["properties"]["value"]




def test_compiled_context_preserves_semantically_selected_long_evidence() -> None:
    root = "Официальный факт " * 3000
    external = [{
        "id": "R1",
        "url": "https://registry.example/company",
        "source_class": "registry",
        "query_kind": "registry",
        "evidence_level": "corroborated_signal",
        "evidence_quote": "Реестровый факт " * 2000,
    }]

    context, stats = focused.compile_company_context(
        url="https://example.org/",
        title="Example",
        text=root,
        external_sources=external,
    )

    payload = __import__("json").loads(context)
    assert payload["official_root_text"] == " ".join(root.split())
    assert payload["documents"][0]["text"] == " ".join(external[0]["evidence_quote"].split())
    assert stats.truncated is False
    assert stats.context_chars == len(context)


@pytest.mark.asyncio
async def test_focused_output_truncation_recovers_by_evidence_boundaries(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_request(phase, model_type, **kwargs):
        assert kwargs["max_tokens"] is None
        marker = "PREPARED EVIDENCE CONTEXT:\n"
        payload = __import__("json").loads(kwargs["prompt"].split(marker, 1)[1])
        calls.append(payload)
        documents = payload.get("documents") or []
        if len(documents) > 1:
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        source_id = documents[0]["id"] if documents else "S1"
        return model_type(
            summary=f"slice-{source_id}",
            company_facts=[{
                "field": "legal_name",
                "value": f"Company {source_id}",
                "confidence": "Высокая",
                "source_ids": [source_id],
            }],
        )

    monkeypatch.setattr(focused, "request_json_strict", fake_request)
    context = __import__("json").dumps({
        "official_url": "https://example.org/",
        "official_root_source_id": "S1",
        "title": "Example",
        "official_root_text": "",
        "documents": [
            {"id": "R1", "text": "Registry identity"},
            {"id": "R2", "text": "Second registry identity"},
        ],
    }, ensure_ascii=False, separators=(",", ":"))

    outcome = await focused._run_focused_pass_resilient(
        focus="identity_governance",
        model_type=focused.IdentityFocusedSlice,
        instructions="identity only",
        context=context,
    )

    assert outcome.recovered is True
    assert outcome.subdivisions == 1
    assert outcome.recovery_failures == ()
    assert len(outcome.results) == 2
    assert {item.company_facts[0].source_ids[0] for item in outcome.results} == {"R1", "R2"}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_focused_truncation_preserves_successful_children_when_one_leaf_is_indivisible(monkeypatch) -> None:
    async def fake_request(phase, model_type, **kwargs):
        marker = "PREPARED EVIDENCE CONTEXT:\n"
        payload = __import__("json").loads(kwargs["prompt"].split(marker, 1)[1])
        documents = payload.get("documents") or []
        if len(documents) > 1:
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        source_id = documents[0]["id"] if documents else "S1"
        if source_id == "R2":
            raise SplitSynthesisPhaseError(phase, "OutputTruncated")
        return model_type(
            summary="kept child",
            company_facts=[{
                "field": "legal_name",
                "value": "Company R1",
                "confidence": "Высокая",
                "source_ids": ["R1"],
            }],
        )

    monkeypatch.setattr(focused, "request_json_strict", fake_request)
    context = __import__("json").dumps({
        "official_url": "https://example.org/",
        "official_root_source_id": "S1",
        "title": "Example",
        "official_root_text": "",
        "documents": [
            {"id": "R1", "text": "one indivisible evidence unit"},
            {"id": "R2", "text": "another indivisible evidence unit"},
        ],
    }, ensure_ascii=False, separators=(",", ":"))

    outcome = await focused._run_focused_pass_resilient(
        focus="identity_governance",
        model_type=focused.IdentityFocusedSlice,
        instructions="identity only",
        context=context,
    )

    assert len(outcome.results) == 1
    assert outcome.results[0].company_facts[0].source_ids == ["R1"]
    assert "OutputTruncated@indivisible_evidence" in outcome.recovery_failures
