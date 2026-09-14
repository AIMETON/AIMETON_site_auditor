from __future__ import annotations

import asyncio
import json

import pytest

import app.routerai_split_synthesis as split
from app.models import (
    ActionPackage,
    AgentRecommendation,
    BusinessMachineCell,
    CommercialOpportunity,
    CompanyFact,
    EconomicSignal,
)
from app.routerai_runtime import routerai_split_synthesis_enabled


def _commercial() -> split.CommercialSynthesis:
    return split.CommercialSynthesis(
        commercial_opportunity=CommercialOpportunity(
            opportunity_type="AI-аудит",
            problem_hypothesis="Есть ручной аналитический контур",
            recommended_solution="Пилот AIMETON",
            expected_value="Сокращение ручной подготовки",
            score=70,
            qualification="Перспективная",
        ),
        agents=[
            AgentRecommendation(name="A1", purpose="Поиск", benefit="Скорость"),
            AgentRecommendation(name="A2", purpose="Анализ", benefit="Проверяемость"),
            AgentRecommendation(name="A3", purpose="Отчёт", benefit="Структура"),
        ],
        action_package=ActionPackage(
            decision_maker_hypothesis="Руководитель цифровизации",
            contact_reason="Показать проверяемый пилот",
            demo_scenario=["Запустить аудит", "Показать evidence"],
            first_message="Предлагаем короткий пилот.",
            next_action="Назначить демонстрацию",
        ),
    )


def _profile() -> split.ProfileExtraction:
    return split.ProfileExtraction(
        company_name="Example",
        business_summary="Тестовая компания",
        evidence=["Есть официальный сайт"],
        company_facts=[
            CompanyFact(
                field="website",
                value="https://example.com",
                confidence="Высокая",
                source_ids=["S1", "E1", "GHOST"],
            )
        ],
        economic_signals=[
            EconomicSignal(
                signal="Сигнал",
                evidence="Источник E1",
                business_effect="Есть возможность автоматизации",
                confidence="Средняя",
                source_ids=["E1"],
            )
        ],
        risks_and_assumptions=["Нужна проверка первичного документа"],
    )


def _km() -> split.BusinessMachineSynthesis:
    return split.BusinessMachineSynthesis(
        business_machine_4x4=[
            BusinessMachineCell(
                code="I-I",
                detail_operator="II — Люди",
                vertex="Учредители и собственники",
                finding="Есть внешние взаимодействия",
                status="Частично",
                confidence="Средняя",
                source_ids=["E1", "GHOST"],
                sales_relevance="Точка входа для пилота",
            )
        ]
    )


def test_split_synthesis_defaults_on_and_has_one_switch_rollback(monkeypatch) -> None:
    monkeypatch.delenv("ROUTERAI_SPLIT_SYNTHESIS", raising=False)
    assert routerai_split_synthesis_enabled() is True

    for disabled in ("0", "false", "NO", "off"):
        monkeypatch.setenv("ROUTERAI_SPLIT_SYNTHESIS", disabled)
        assert routerai_split_synthesis_enabled() is False

    monkeypatch.setenv("ROUTERAI_SPLIT_SYNTHESIS", "1")
    assert routerai_split_synthesis_enabled() is True


def test_profile_extraction_collections_are_bounded() -> None:
    schema = split.ProfileExtraction.model_json_schema()
    properties = schema["properties"]
    assert properties["evidence"]["maxItems"] == 12
    assert properties["company_facts"]["maxItems"] == 30
    assert properties["economic_signals"]["maxItems"] == 16
    assert properties["risks_and_assumptions"]["maxItems"] == 16


def test_request_json_uses_provider_json_mode(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "company_name": "Example",
                                    "business_summary": "Summary",
                                    "evidence": [],
                                    "company_facts": [],
                                    "economic_signals": [],
                                    "risks_and_assumptions": [],
                                }
                            )
                        },
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only")
    monkeypatch.setattr(split.httpx, "AsyncClient", lambda timeout: FakeClient())

    result = asyncio.run(
        split._request_json(
            "profile_extraction",
            split.ProfileExtraction,
            system="JSON only",
            prompt="Extract",
            max_tokens=100,
            timeout_seconds=1,
        )
    )

    assert result.company_name == "Example"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_request_json_surfaces_output_truncation(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "{}"},
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only")
    monkeypatch.setattr(split.httpx, "AsyncClient", lambda timeout: FakeClient())

    with pytest.raises(split.SplitSynthesisPhaseError) as exc_info:
        asyncio.run(
            split._request_json(
                "profile_extraction",
                split.ProfileExtraction,
                system="JSON only",
                prompt="Extract",
                max_tokens=100,
                timeout_seconds=1,
            )
        )

    assert exc_info.value.phase == "profile_extraction"
    assert exc_info.value.error_type == "OutputTruncated"


def test_split_assembly_filters_unknown_sources_and_enforces_km_canon() -> None:
    result = split._assemble_site_analysis(
        url="https://example.com",
        title="Example",
        text="Official text",
        external_sources=[
            {
                "id": "E1",
                "title": "External",
                "url": "https://external.example/item",
                "accessed_at": "2026-08-16T00:00:00+00:00",
                "snippet": "Discovery snippet",
                "source_type": "news",
                "evidence_level": "unverified_mention",
            },
            {
                "id": "E2",
                "title": "Unused",
                "url": "https://external.example/unused",
                "accessed_at": "2026-08-16T00:00:00+00:00",
                "snippet": "Unused snippet",
            },
        ],
        profile=_profile(),
        km=_km(),
        commercial=_commercial(),
        accessed_at="2026-08-16T00:00:00+00:00",
    )

    assert [source.id for source in result.sources] == ["S1"]
    assert result.company_facts[0].source_ids == ["S1"]
    assert result.economic_signals[0].source_ids == []
    assert result.business_machine_4x4[0].source_ids == []
    assert result.business_machine_4x4[0].detail_operator == "I — Коммуникационные системы"
    assert result.business_machine_4x4[0].vertex == "Взаимодействие"
    assert result.readiness.provider_states["routerai"] == "active"
    assert result.commercial_opportunity.score == 70


def test_split_pipeline_extracts_once_then_runs_two_reasoning_phases(monkeypatch) -> None:
    phases: list[str] = []

    async def fake_request_json(phase, model_type, **kwargs):
        phases.append(phase)
        if model_type is split.ProfileExtraction:
            return _profile()
        if model_type is split.BusinessMachineSynthesis:
            return _km()
        if model_type is split.CommercialSynthesis:
            return _commercial()
        raise AssertionError(model_type)

    monkeypatch.setattr(split, "_request_json", fake_request_json)

    result = asyncio.run(
        split.analyze_with_routerai_split(
            "https://example.com",
            "Example",
            "Official text",
            [
                {
                    "id": "E1",
                    "title": "External",
                    "url": "https://external.example/item",
                    "accessed_at": "2026-08-16T00:00:00+00:00",
                    "snippet": "Discovery snippet",
                    "source_type": "news",
                    "evidence_level": "unverified_mention",
                }
            ],
        )
    )

    assert phases[0] == "profile_extraction"
    assert set(phases[1:]) == {"km_reasoning", "commercial_reasoning"}
    assert result.company_name == "Example"
    assert result.commercial_opportunity.qualification == "Перспективная"
    assert result.business_machine_4x4[0].code == "I-I"
