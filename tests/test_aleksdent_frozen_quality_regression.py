from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evidence_source_projection import collapse_verified_evidence
from app.evidence_triage import EntityRelation, deterministic_block_decision
from app.external_sources import IdentityAnchors, query_plan
from app.external_verification import document_matches_entity
from app.models import CompanyFact, EconomicSignal, IntelligenceSource
from app.profile_consolidation import consolidate_merged_profile
from app.reasoning_dossier import build_reasoning_dossier
from app.research_coverage_controller import (
    DEFAULT_DEEP_RESULTS_PER_QUERY,
    initial_wave,
)
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import MergedProfileExtraction


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "aleksdent_quality_regression.json"


@pytest.fixture(scope="module")
def case() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _anchors(case: dict) -> IdentityAnchors:
    target = case["target"]
    return IdentityAnchors(
        domain=target["domain"],
        legal_name=target["legal_name"],
        inn=target["inn"],
        cities=(target["city"],),
    )


def test_frozen_case_search_fanout_is_bounded_before_fetch(case: dict) -> None:
    plan = query_plan(case["target"]["company_name"], anchors=_anchors(case))
    wave = initial_wave(plan)
    acceptance = case["acceptance"]

    assert len(plan) == 20
    assert len(wave) == acceptance["max_initial_core_queries"] == 6
    assert DEFAULT_DEEP_RESULTS_PER_QUERY == acceptance["max_deep_results_per_query"] == 8
    current_initial_ceiling = len(wave) * DEFAULT_DEEP_RESULTS_PER_QUERY
    assert current_initial_ceiling == acceptance["max_initial_discovery_results"] == 48

    # The old deep policy was 20 queries × 100 results before relevance was known.
    old_initial_ceiling = len(plan) * 100
    assert old_initial_ceiling / current_initial_ceiling > 40

    baseline = case["bad_run_baseline"]
    assert baseline["documents"] == 586
    assert baseline["llm_calls"] == 680
    assert baseline["tokens"] == 3_551_120


def test_frozen_case_rejects_foreign_primary_entity_and_publisher_footer(case: dict) -> None:
    foreign = case["foreign_primary_document"]
    blocks = [
        SimpleNamespace(text=item["text"], locator=item["locator"])
        for item in foreign["blocks"]
    ]
    matches, reason = document_matches_entity(
        "\n".join(item.text for item in blocks),
        company_name=case["target"]["company_name"],
        anchors=_anchors(case),
        document_url=foreign["url"],
        blocks=blocks,
        document_title=foreign["title"],
    )

    assert matches is case["acceptance"]["foreign_document_must_verify"] is False
    assert reason == "identity_not_confirmed"

    directory = case["directory_document"]
    footer = directory["publisher_footer"]
    footer_decision = deterministic_block_decision(
        block_id="B-footer",
        text=footer["text"],
        locator=footer["locator"],
        company_name=case["target"]["company_name"],
        anchors=_anchors(case),
        source_query_kind="review",
        source_is_official=False,
    )
    assert footer_decision is not None
    assert footer_decision.keep is case["acceptance"]["publisher_footer_must_be_kept"] is False
    assert footer_decision.entity_relation is EntityRelation.PUBLISHER

    target_block = directory["target_block"]
    target_decision = deterministic_block_decision(
        block_id="B-target",
        text=target_block["text"],
        locator=target_block["locator"],
        company_name=case["target"]["company_name"],
        anchors=_anchors(case),
        source_query_kind="review",
        source_is_official=False,
    )
    assert target_decision is not None
    assert target_decision.keep is True
    assert target_decision.entity_relation is EntityRelation.TARGET
    assert target_decision.query_kind == "other"


def _digest(number: int) -> str:
    return "sha256:" + f"{number:064x}"


def test_frozen_case_public_sources_stay_document_level(case: dict) -> None:
    directory = case["directory_document"]
    accessed = "2026-09-18T00:00:00+00:00"
    parent = IntelligenceSource(
        id="H1",
        title=directory["title"],
        url=directory["url"],
        accessed_at=accessed,
        source_class="review",
        query_kind="review",
        lifecycle_state="evidence",
        evidence_level="weak_signal",
        document_url=directory["url"],
        document_title=directory["title"],
        document_accessed_at=accessed,
        document_digest=_digest(1),
        evidence_quote=directory["target_block"]["text"],
        evidence_locator=directory["target_block"]["locator"],
        evidence_digest=_digest(2),
        fetch_path="static",
        verification_note="Primary identity confirmed.",
    )

    # One of the real bad-run foreign pages alone produced ~347 public block cards.
    # Even if 347 retained blocks exist internally, the public boundary is one document.
    children = []
    for index in range(347):
        child = parent.model_copy(deep=True)
        child.id = f"H1-b{index}-0"
        child.query_kind = "other"
        child.evidence_quote = f"Релевантный блок Алекс Дент #{index}"
        child.evidence_locator = f"main/section[{index}]"
        child.evidence_digest = _digest(1_000 + index)
        child.verification_note = (
            "Primary identity confirmed. "
            "Evidence triage: target/other; target_name_in_block."
        )
        children.append(child)

    projected = collapse_verified_evidence([parent, *children])

    assert len(projected) == case["acceptance"]["public_sources_per_document"] == 1
    assert projected[0].id == "H1"
    assert len(projected[0].evidence_blocks) == 347
    assert all("-b" not in source.id for source in projected)

    baseline = case["bad_run_baseline"]
    assert baseline["public_source_cards"] == 21_149
    assert baseline["unique_source_urls"] == 90
    assert baseline["block_derived_source_cards"] / baseline["public_source_cards"] > 0.99


def _raw_facts(case: dict) -> list[CompanyFact]:
    distribution = case["fact_distribution"]
    facts: list[CompanyFact] = []

    for index in range(distribution["other"]):
        value = "Нет данных" if index == 0 else f"Прочий подтвержденный факт {index}"
        facts.append(CompanyFact(field="other", value=value, source_ids=["S1"]))

    for index in range(distribution["products"]):
        facts.append(
            CompanyFact(
                field="products",
                value=f"Стоматологическая услуга {index}",
                confidence="Высокая" if index < 20 else "Средняя",
                source_ids=["S1"],
            )
        )

    for index in range(36):
        facts.append(
            CompanyFact(
                field="executives",
                value=f"Целевой руководитель {index}",
                source_ids=["S1"],
            )
        )
    facts.append(
        CompanyFact(
            field="executives",
            value="ЦЕЛЕВОЙ РУКОВОДИТЕЛЬ 0",
            confidence="Высокая",
            source_ids=["S1"],
        )
    )
    facts.append(
        CompanyFact(
            field="executives",
            value="ООО «ЗУН»",
            source_ids=["Z-b8-0"],
        )
    )

    for index in range(distribution["founders"]):
        facts.append(
            CompanyFact(field="founders", value=f"Учредитель {index}", source_ids=["S1"])
        )
    for index in range(distribution["beneficial_owners"]):
        facts.append(
            CompanyFact(
                field="beneficial_owners",
                value=f"Бенефициар {index}",
                source_ids=["S1"],
            )
        )
    for index in range(distribution["social_accounts"]):
        facts.append(
            CompanyFact(
                field="social_accounts",
                value=f"https://social.example/aleksdent/{index}",
                source_ids=["S1"],
            )
        )
    for index in range(distribution["affiliates"]):
        facts.append(
            CompanyFact(
                field="affiliates",
                value=f"Связанная компания {index}",
                source_ids=["S1"],
            )
        )
    for index in range(distribution["registration_status"]):
        facts.append(
            CompanyFact(
                field="registration_status",
                value=f"Статус регистрации {index}",
                source_ids=["S1"],
            )
        )

    singles = {
        "brand_name": "Алекс Дент",
        "website": "https://aleksdent24.ru/",
        "phones": "+7 000 000-00-00",
        "geography": "Красноярск",
        "address": "Красноярск",
        "headcount": "Не менее 1 сотрудника",
        "legal_name": case["target"]["legal_name"],
        "inn": case["target"]["inn"],
        "ogrn": "0000000000000",
    }
    assert set(singles) == set(distribution["single_fields"])
    for field, value in singles.items():
        facts.append(CompanyFact(field=field, value=value, source_ids=["S1"]))

    assert len(facts) == case["bad_run_baseline"]["company_facts"] == 451
    return facts


def test_frozen_case_consolidation_and_reasoning_dossier_are_bounded(case: dict) -> None:
    raw_facts = _raw_facts(case)
    raw_signals = [
        EconomicSignal(
            signal=f"Экономический сигнал {index}",
            evidence=f"Подтверждение {index}",
            business_effect=f"Эффект {index}",
            confidence="Высокая" if index < 8 else "Средняя",
            source_ids=["S1"],
        )
        for index in range(60)
    ]
    coverage = EvidenceCoverage(
        official_chars_total=120_000,
        official_chunks_total=10,
        official_chunks_processed=10,
        sources_total=90,
        sources_processed=90,
        source_chunks_total=120,
        source_chunks_processed=120,
        extraction_units_total=140,
        extraction_units_processed=140,
        complete=True,
    )
    merged = MergedProfileExtraction(
        company_name=case["target"]["company_name"],
        business_summary="Стоматологическая клиника в Красноярске",
        evidence=[f"Evidence highlight {index}" for index in range(30)],
        company_facts=raw_facts,
        economic_signals=raw_signals,
        risks_and_assumptions=[f"Risk {index}" for index in range(30)],
        coverage=coverage,
    )
    external_sources = [
        {
            "id": "Z-b8-0",
            "query_kind": "ownership",
            "lifecycle_state": "evidence",
            "verification_note": (
                "Primary fetched. "
                "Evidence triage: publisher/ownership; third_party_publisher_container."
            ),
        }
    ]

    clean, stats = consolidate_merged_profile(
        merged,
        external_sources=external_sources,
    )

    assert stats.placeholders_removed == 1
    assert stats.semantic_duplicates_merged == 1
    assert stats.foreign_sensitive_facts_rejected == 1
    assert len(clean.company_facts) == 448
    assert all(fact.value != "Нет данных" for fact in clean.company_facts)
    assert all(fact.value != "ООО «ЗУН»" for fact in clean.company_facts)

    profile = SimpleNamespace(
        company_name=clean.company_name,
        business_summary=clean.business_summary,
        evidence=clean.evidence,
        company_facts=clean.company_facts,
        economic_signals=clean.economic_signals,
        risks_and_assumptions=clean.risks_and_assumptions,
        coverage=clean.coverage.safe_dict(),
    )
    dossier = build_reasoning_dossier(profile)

    acceptance = case["acceptance"]
    assert len(dossier.facts_by_field["products"]) == acceptance["max_reasoning_products"] == 40
    assert len(dossier.facts_by_field["other"]) == acceptance["max_reasoning_other"] == 20
    assert len(dossier.facts_by_field["executives"]) == acceptance["max_reasoning_executives"] == 32
    assert dossier.omitted_fact_counts_by_field["products"] == 140
    assert dossier.omitted_fact_counts_by_field["other"] == 163
    assert dossier.omitted_fact_counts_by_field["executives"] == 4
    assert dossier.total_facts == 448
    assert dossier.selected_facts < dossier.total_facts
    assert dossier.total_signals == 60
    assert dossier.selected_signals == 24
    assert len(dossier.evidence_highlights) == 12
    assert len(dossier.risks_and_assumptions) == 16
