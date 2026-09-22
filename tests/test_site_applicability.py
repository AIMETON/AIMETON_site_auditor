import pytest

from app.fast_research_model import FastResearchModelUnavailable
from app.models import TargetApplicability
from app.site_applicability import (
    classify_site_applicability,
    not_applicable_site_analysis,
    should_short_circuit,
)


@pytest.mark.asyncio
async def test_classifier_marks_media_article_not_applicable() -> None:
    async def request(phase, model_type, **kwargs):
        assert phase == "site_applicability"
        assert "не делай коммерческих выводов" in kwargs["system"]
        return model_type(
            applicability="not_applicable",
            target_kind="media_article",
            confidence=0.97,
            target_entity_name="Новостное издание",
            reason="Страница является редакционной статьёй и лишь упоминает компании.",
        )

    result = await classify_site_applicability(
        "https://news.example/article",
        "Компания X представила продукт | Новости",
        "Авторская статья о запуске продукта компанией X.",
        request_json=request,
    )

    assert result.applicability == "not_applicable"
    assert result.target_kind == "media_article"
    assert should_short_circuit(result) is True


@pytest.mark.asyncio
async def test_classifier_unavailable_fails_open() -> None:
    async def request(*args, **kwargs):
        raise FastResearchModelUnavailable("offline")

    result = await classify_site_applicability(
        "https://example.org/",
        "Example",
        "Компания оказывает услуги клиентам.",
        request_json=request,
    )

    assert result.applicability == "unavailable"
    assert result.target_kind == "unknown"
    assert should_short_circuit(result) is False


def test_non_company_result_contains_no_commercial_agents() -> None:
    assessment = TargetApplicability(
        applicability="not_applicable",
        target_kind="directory_aggregator",
        confidence=0.94,
        target_entity_name="Каталог организаций",
        reason="Это агрегатор карточек разных организаций.",
    )

    result = not_applicable_site_analysis(
        "https://directory.example/",
        "Каталог организаций",
        assessment,
    )

    assert result.target_applicability == assessment
    assert result.agents == []
    assert result.company_facts == []
    assert result.economic_signals == []
    assert result.commercial_opportunity.score == 0
    assert result.readiness.analysis_state == "not_applicable"
    assert "target_not_company_site" in result.readiness.release_blockers
    assert result.research_status["core_llm_calls"] == 0


def test_low_confidence_not_applicable_does_not_short_circuit() -> None:
    assessment = TargetApplicability(
        applicability="not_applicable",
        target_kind="documentation_knowledge_base",
        confidence=0.55,
        reason="Ownership is unclear.",
    )

    assert should_short_circuit(assessment) is False
