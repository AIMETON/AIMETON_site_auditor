import pytest

from app.models import CompanyFact
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import MergedProfileExtraction
from app import routerai_split_v2 as split


def merged_profile():
    return MergedProfileExtraction(
        company_name="Алекс Дент",
        business_summary="Стоматологическая клиника",
        evidence=["Официальный сайт"],
        company_facts=[
            CompanyFact(field="legal_name", value="ООО «Алекс Дент»", source_ids=["S1"]),
            CompanyFact(field="legal_name", value='ООО "АЛЕКС ДЕНТ"', source_ids=["S1"]),
            CompanyFact(field="other", value="Нет данных", source_ids=["S1"]),
        ],
        economic_signals=[],
        risks_and_assumptions=[],
        coverage=EvidenceCoverage(
            official_chars_total=100,
            official_chunks_total=1,
            official_chunks_processed=1,
            sources_total=0,
            sources_processed=0,
            source_chunks_total=0,
            source_chunks_processed=0,
            extraction_units_total=5,
            extraction_units_processed=5,
            complete=True,
        ),
    )


@pytest.mark.asyncio
async def test_reasoning_failure_preserves_clean_profile_without_heuristic_score(monkeypatch):
    raw = merged_profile()
    persisted = []
    seen = {}

    async def extract(**kwargs):
        return raw

    def persist(value):
        persisted.append(value)

    async def fail(url, title, text, external_sources, profile, dossier, accessed_at):
        seen["profile"] = profile
        seen["dossier"] = dossier
        raise RuntimeError("provider_failed")

    monkeypatch.setattr(split, "extract_profile_parallel", extract)
    monkeypatch.setattr(split, "persist_merged_evidence_ledger", persist)
    monkeypatch.setattr(split, "_reason_and_assemble", fail)

    result = await split.analyze_with_routerai_split_v2(
        "https://example.org",
        "Алекс Дент",
        "официальный текст",
        [],
    )

    assert persisted == [raw]
    assert len(persisted[0].company_facts) == 3  # durable raw ledger is untouched
    assert len(seen["profile"].company_facts) == 1  # consolidated profile remains complete
    assert seen["dossier"].selected_facts == 1
    assert [fact.value for fact in result.company_facts] == ["ООО «Алекс Дент»"]
    assert result.commercial_opportunity.score == 0
    assert result.commercial_opportunity.qualification == "Недостаточно данных"
    assert len(result.agents) == 3
    assert all(agent.name == "AI-рекомендация не рассчитана" for agent in result.agents)
    assert result.research_status["commercial_reasoning_state"] == "failed"
    assert result.research_status["commercial_score_available"] is False
    assert result.research_status["profile_consolidation"]["semantic_duplicates_merged"] == 1
    assert result.research_status["profile_consolidation"]["placeholders_removed"] == 1
    assert result.research_status["reasoning_dossier_total_facts"] == 1
    assert result.research_status["reasoning_dossier_selected_facts"] == 1
    assert result.research_status["reasoning_dossier_omitted_facts"] == 0
    assert result.readiness.provider_states["routerai"] == "reasoning_failed_extraction_preserved"
    assert result.readiness.commercial_priority == 0
    assert "commercial_reasoning_incomplete" in result.readiness.release_blockers
