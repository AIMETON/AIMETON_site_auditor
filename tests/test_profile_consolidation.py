import pytest

from app.models import CompanyFact, EconomicSignal
from app.profile_consolidation import consolidate_facts, consolidate_merged_profile, consolidate_signals
from app.routerai_evidence_units import EvidenceCoverage
from app.routerai_profile_extraction import MergedProfileExtraction


def test_semantic_fact_dedup_merges_sources_and_removes_placeholders():
    facts = [
        CompanyFact(field="legal_name", value="ООО «Алекс Дент»", confidence="Средняя", source_ids=["S1"]),
        CompanyFact(field="legal_name", value='ООО "АЛЕКС ДЕНТ"', confidence="Высокая", source_ids=["H1-b2-0"]),
        CompanyFact(field="other", value="Нет данных", source_ids=["S1"]),
    ]
    sources = [{
        "id": "H1-b2-0",
        "source_class": "registry",
        "query_kind": "registry",
        "lifecycle_state": "evidence",
        "verification_note": "Первичный документ загружен. Evidence triage: target/registry; strong_target_anchor_in_block.",
    }]

    merged, placeholders, duplicates, foreign = consolidate_facts(facts, external_sources=sources)

    assert len(merged) == 1
    assert merged[0].field == "legal_name"
    assert merged[0].confidence == "Высокая"
    assert merged[0].source_ids == ["S1", "H1"]
    assert placeholders == 1
    assert duplicates == 1
    assert foreign == 0


def test_sensitive_fact_from_publisher_only_provenance_is_rejected():
    facts = [
        CompanyFact(field="executives", value="ООО ЗУН", source_ids=["Z-b8-0"]),
        CompanyFact(field="executives", value="Иванов Иван Иванович", source_ids=["R-b1-0"]),
    ]
    sources = [
        {
            "id": "Z-b8-0",
            "query_kind": "ownership",
            "lifecycle_state": "evidence",
            "verification_note": "Primary fetched. Evidence triage: publisher/ownership; third_party_publisher_container.",
        },
        {
            "id": "R-b1-0",
            "query_kind": "ownership",
            "lifecycle_state": "evidence",
            "verification_note": "Primary fetched. Evidence triage: target/ownership; target_name_in_block.",
        },
    ]

    merged, _, _, foreign = consolidate_facts(facts, external_sources=sources)

    assert [fact.value for fact in merged] == ["Иванов Иван Иванович"]
    assert merged[0].source_ids == ["R"]
    assert foreign == 1


def test_signal_dedup_keeps_one_normalized_signal_and_parent_sources():
    signals = [
        EconomicSignal(signal="Рост выручки", evidence="Выручка выросла", business_effect="Есть бюджет", source_ids=["F-b1-0"]),
        EconomicSignal(signal="Рост   выручки", evidence="Выручка выросла", business_effect="Есть бюджет", source_ids=["F-b2-0"]),
        EconomicSignal(signal="Нет данных", evidence="Нет данных", business_effect="Нет данных"),
    ]

    merged = consolidate_signals(signals)

    assert len(merged) == 1
    assert merged[0].source_ids == ["F"]


def test_merged_profile_prefers_consolidated_brand_over_first_chunk_seo_title():
    coverage = EvidenceCoverage(
        official_chars_total=100, official_chunks_total=1, official_chunks_processed=1,
        sources_total=1, sources_processed=1, source_chunks_total=1, source_chunks_processed=1,
        extraction_units_total=2, extraction_units_processed=2, complete=True,
    )
    merged = MergedProfileExtraction(
        company_name="Стоматология Красноярск цены доступные для частной клиники",
        business_summary="Стоматологическая клиника",
        evidence=[],
        company_facts=[
            CompanyFact(field="legal_name", value='ООО "АЛЕКС ДЕНТ"', confidence="Высокая", source_ids=["R1"]),
            CompanyFact(field="brand_name", value="Алекс Дент", confidence="Высокая", source_ids=["S1"]),
        ],
        economic_signals=[],
        risks_and_assumptions=[],
        coverage=coverage,
    )

    clean, _ = consolidate_merged_profile(merged, external_sources=[])

    assert clean.company_name == "Алекс Дент"
    assert {fact.field for fact in clean.company_facts} == {"legal_name", "brand_name"}


def test_merged_profile_drops_orphan_prices_but_keeps_labeled_commercial_facts():
    coverage = EvidenceCoverage(
        official_chars_total=100, official_chunks_total=1, official_chunks_processed=1,
        sources_total=1, sources_processed=1, source_chunks_total=1, source_chunks_processed=1,
        extraction_units_total=2, extraction_units_processed=2, complete=True,
    )
    merged = MergedProfileExtraction(
        company_name="Алекс Дент",
        business_summary="Стоматологическая клиника",
        evidence=[],
        company_facts=[
            CompanyFact(field="other", value="от 1 200 руб.", source_ids=["S1"]),
            CompanyFact(field="other", value="69 000 ₽", source_ids=["S1"]),
            CompanyFact(field="other", value="Имплантация под ключ — от 69 000 ₽", source_ids=["S1"]),
            CompanyFact(field="products", value="Имплантация", source_ids=["S1"]),
        ],
        economic_signals=[],
        risks_and_assumptions=[],
        coverage=coverage,
    )

    clean, stats = consolidate_merged_profile(merged, external_sources=[])

    assert [fact.value for fact in clean.company_facts] == [
        "Имплантация под ключ — от 69 000 ₽",
        "Имплантация",
    ]
    assert stats.low_information_other_removed == 2
    assert stats.product_facts_input == 1
    assert stats.product_facts_output == 1
    assert stats.other_facts_input == 3
    assert stats.other_facts_output == 1


def test_product_dedup_ignores_price_and_promo_variants_but_keeps_distinct_services():
    facts = [
        CompanyFact(field="products", value="Имплантация под ключ — от 69 000 ₽", source_ids=["S1"]),
        CompanyFact(field="products", value="Имплантация под ключ — 72 000 руб.", source_ids=["P1"]),
        CompanyFact(field="products", value="Имплантация под ключ (акция -20%)", source_ids=["P2"]),
        CompanyFact(field="products", value="Имплантация All-on-4 — от 180 000 ₽", source_ids=["S1"]),
    ]

    merged, placeholders, duplicates, foreign = consolidate_facts(
        facts,
        external_sources=[],
    )

    assert placeholders == 0
    assert foreign == 0
    assert duplicates == 2
    assert len(merged) == 2
    assert merged[0].value == "Имплантация под ключ — от 69 000 ₽"
    assert merged[0].source_ids == ["S1", "P1", "P2"]
    assert merged[1].value == "Имплантация All-on-4 — от 180 000 ₽"

    coverage = EvidenceCoverage(
        official_chars_total=100, official_chunks_total=1, official_chunks_processed=1,
        sources_total=1, sources_processed=1, source_chunks_total=1, source_chunks_processed=1,
        extraction_units_total=2, extraction_units_processed=2, complete=True,
    )
    profile = MergedProfileExtraction(
        company_name="Алекс Дент",
        business_summary="Стоматологическая клиника",
        evidence=[],
        company_facts=facts,
        economic_signals=[],
        risks_and_assumptions=[],
        coverage=coverage,
    )
    clean, stats = consolidate_merged_profile(profile, external_sources=[])
    assert stats.product_facts_input == 4
    assert stats.product_facts_output == 2
    assert len([fact for fact in clean.company_facts if fact.field == "products"]) == 2
