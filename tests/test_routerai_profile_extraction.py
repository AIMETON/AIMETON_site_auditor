from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

import app.routerai_profile_extraction as profile
from app.models import CompanyFact
from app.routerai_evidence_units import (
    DEFAULT_EVIDENCE_CHUNK_CHARS,
    EvidenceCoverageOverflow,
    chunk_sources,
    chunk_text,
    evidence_units,
    project_sources,
)


def _source(source_id: str, query_kind: str, *, snippet: str | None = None) -> dict:
    return {
        "id": source_id,
        "title": f"Source {source_id}",
        "query_kind": query_kind,
        "source_class": query_kind,
        "evidence_level": "unverified_mention",
        "snippet": snippet or f"snippet-{source_id}",
        "url": f"https://example.test/{source_id}",
    }


def test_source_slices_follow_search_verticals_keep_ids_and_never_prefix_truncate() -> None:
    late = "LATE-EVIDENCE-" + "x" * 16000
    sources = [
        _source("E1", "contact"),
        _source("E2", "ownership"),
        _source("E3", "affiliation"),
        _source("E4", "finance"),
        _source("E5", "court", snippet=late),
        _source("E6", "jobs"),
    ]

    identity_core = json.loads(profile._source_slice(sources, profile._IDENTITY_CORE_KINDS))
    management = json.loads(profile._source_slice(sources, profile._MANAGEMENT_KINDS))
    ownership_network = json.loads(profile._source_slice(sources, profile._OWNERSHIP_NETWORK_KINDS))
    operations = json.loads(profile._source_slice(sources, profile._OPERATIONS_KINDS))
    signals_raw = profile._source_slice(sources, profile._SIGNAL_KINDS, char_budget=100)
    signals = json.loads(signals_raw)

    assert {item["id"] for item in identity_core} == {"E1"}
    assert {item["id"] for item in management} == {"E2"}
    assert {item["id"] for item in ownership_network} == {"E2", "E3"}
    assert {item["id"] for item in operations} == {"E4", "E6"}
    assert {item["id"] for item in signals} == {"E4", "E5"}
    assert late in signals_raw
    assert all(
        "url" in item
        for item in identity_core + management + ownership_network + operations + signals
    )


def test_chunk_primitives_preserve_all_text_and_late_sources() -> None:
    text = "A" * (DEFAULT_EVIDENCE_CHUNK_CHARS + 7) + "TAIL_FACT"
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert "".join(chunks) == text
    assert chunks[-1].endswith("TAIL_FACT")

    sources = [
        _source("E1", "finance", snippet="a" * 7000),
        _source("E2", "finance", snippet="b" * 7000),
        _source("E3", "finance", snippet="late-source-marker"),
    ]
    projected = project_sources(sources, profile._OPERATIONS_KINDS, profile._SLICE_SOURCE_KEYS)
    source_chunks = chunk_sources(projected)
    recovered = [item for raw in source_chunks for item in json.loads(raw)]
    assert [item["id"] for item in recovered] == ["E1", "E2", "E3"]
    assert recovered[-1]["snippet"] == "late-source-marker"


def test_official_text_is_not_multiplied_across_all_extraction_verticals() -> None:
    neutral_a = "services and prices " + "a" * (DEFAULT_EVIDENCE_CHUNK_CHARS - 20)
    management = "Наш генеральный директор Иван Иванов " + "b" * (DEFAULT_EVIDENCE_CHUNK_CHARS - 40)
    neutral_c = "contacts and schedule " + "c" * 100
    text = neutral_a + management + neutral_c

    projected = {name: [] for name in ("identity", "management", "ownership", "operations", "signals")}
    units = {
        name: profile._slice_evidence_units(text, sources, name)
        for name, sources in projected.items()
    }

    official_chunks = chunk_text(text)
    assert len(units["identity"]) == len(official_chunks)
    assert len(units["operations"]) == len(official_chunks)
    assert sum(bool(official) for official, _ in units["management"]) == 1
    assert sum(bool(official) for official, _ in units["ownership"]) == 0
    assert sum(bool(official) for official, _ in units["signals"]) == 0
    assert sum(len(items) for items in units.values()) < len(official_chunks) * 5


def test_narrow_official_routing_keeps_first_party_ownership_and_signal_chunks() -> None:
    ownership = "Учредитель и собственник клиники указан на странице. Telegram: t.me/example"
    signal = "Новости клиники и отзывы пациентов. Судебных споров не указано."
    filler = "x" * DEFAULT_EVIDENCE_CHUNK_CHARS
    text = ownership + filler + signal

    ownership_units = profile._slice_evidence_units(text, [], "ownership")
    signal_units = profile._slice_evidence_units(text, [], "signals")

    assert any("Учредитель" in official for official, _ in ownership_units)
    assert any("отзывы" in official for official, _ in signal_units)


def test_vertical_dto_bounds_keep_each_chunk_compact() -> None:
    fact_schema = profile.CompactCompanyFact.model_json_schema()
    management_fact_schema = profile.ManagementCompanyFact.model_json_schema()
    ownership_fact_schema = profile.OwnershipNetworkCompanyFact.model_json_schema()
    identity_schema = profile.IdentityCoreSlice.model_json_schema()
    operations_schema = profile.OperationsProfileSlice.model_json_schema()
    signal_slice_schema = profile.SignalProfileSlice.model_json_schema()

    assert fact_schema["properties"]["value"]["maxLength"] == 200
    assert management_fact_schema["properties"]["value"]["maxLength"] == 140
    assert ownership_fact_schema["properties"]["value"]["maxLength"] == 140
    assert identity_schema["properties"]["company_facts"]["maxItems"] == 12
    assert operations_schema["properties"]["company_facts"]["maxItems"] == 12
    assert signal_slice_schema["properties"]["economic_signals"]["maxItems"] == 8


def test_management_dto_rejects_output_outside_bounded_envelope() -> None:
    with pytest.raises(ValidationError):
        profile.ManagementCompanyFact(
            field="executives",
            value="x" * 141,
            source_ids=["S1"],
        )
    with pytest.raises(ValidationError):
        profile.ManagementCompanyFact(
            field="affiliates",
            value="Not a management field",
            source_ids=["S1"],
        )
    with pytest.raises(ValidationError):
        profile.ManagementCompanyFact(
            field="founders",
            value="Founder",
            source_ids=["S" * 65],
        )


def test_large_dossier_processes_beginning_middle_end_and_preserves_periods() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def fake_request(phase, model_type, **kwargs):
        prompt = kwargs["prompt"]
        calls.append((phase, prompt, kwargs))
        if model_type is profile.IdentityCoreSlice:
            facts = []
            evidence = []
            if "BEGIN_FACT" in prompt:
                facts.append(profile.CompactCompanyFact(field="website", value="https://example.com", source_ids=["S1"]))
                evidence.append("begin retained")
            if "END_FACT" in prompt:
                facts.append(profile.CompactCompanyFact(field="geography", value="End-region", source_ids=["S1"]))
                evidence.append("end retained")
            return profile.IdentityCoreSlice(company_name="Example", business_summary="Example", company_facts=facts, evidence=evidence)
        if model_type is profile.ManagementSlice:
            return profile.ManagementSlice()
        if model_type is profile.OwnershipNetworkSlice:
            return profile.OwnershipNetworkSlice()
        if model_type is profile.OperationsProfileSlice:
            facts = []
            if "MIDDLE_2024" in prompt:
                facts.append(profile.CompactCompanyFact(field="revenue", value="100", period="2024", source_ids=["S1"]))
            if "END_FACT" in prompt:
                facts.append(profile.CompactCompanyFact(field="revenue", value="120", period="2025", source_ids=["S1"]))
            if "late-source-marker" in prompt:
                facts.append(profile.CompactCompanyFact(field="customers", value="Late source customer", source_ids=["E9"]))
            return profile.OperationsProfileSlice(company_facts=facts)
        if model_type is profile.SignalProfileSlice:
            return profile.SignalProfileSlice()
        raise AssertionError(model_type)

    text = (
        "BEGIN_FACT\n"
        + "a" * (DEFAULT_EVIDENCE_CHUNK_CHARS + 200)
        + "MIDDLE_2024\n"
        + "b" * (DEFAULT_EVIDENCE_CHUNK_CHARS + 200)
        + "END_FACT"
    )
    merged = asyncio.run(
        profile.extract_profile_parallel(
            request_json=fake_request,
            strict_request_json=fake_request,
            url="https://example.com",
            title="Example",
            text=text,
            external_sources=[_source("E9", "other", snippet="late-source-marker")],
            accessed_at="2026-08-18T00:00:00+00:00",
        )
    )

    fields = {(item.field, item.value, item.period) for item in merged.company_facts}
    assert ("website", "https://example.com", None) in fields
    assert ("geography", "End-region", None) in fields
    assert ("revenue", "100", "2024") in fields
    assert ("revenue", "120", "2025") in fields
    assert ("customers", "Late source customer", None) in fields
    assert merged.coverage.complete is True
    assert merged.coverage.official_chars_total == len(text)
    assert merged.coverage.official_chunks_total >= 3
    assert merged.coverage.official_chunks_processed == merged.coverage.official_chunks_total
    assert merged.coverage.sources_total == 1
    assert merged.coverage.sources_processed == 1
    assert merged.coverage.extraction_units_processed == merged.coverage.extraction_units_total
    assert all(kwargs["reasoning_enabled"] is False for _, _, kwargs in calls)
    assert max(kwargs["max_tokens"] for _, _, kwargs in calls) <= 1100


def test_fast_path_overflow_and_unrouted_sources_fail_before_silent_truncation() -> None:
    huge = "x" * (DEFAULT_EVIDENCE_CHUNK_CHARS * 17)
    with pytest.raises(EvidenceCoverageOverflow):
        evidence_units(huge, [])

    async def must_not_run(*args, **kwargs):
        raise AssertionError("provider must not be called for unrouted evidence")

    with pytest.raises(EvidenceCoverageOverflow, match="unrouted_sources=1"):
        asyncio.run(
            profile.extract_profile_parallel(
                request_json=must_not_run,
                strict_request_json=must_not_run,
                url="https://example.com",
                title="Example",
                text="Official",
                external_sources=[_source("E-X", "unknown-kind")],
                accessed_at="2026-08-18T00:00:00+00:00",
            )
        )


def test_parallel_extractors_merge_into_public_fact_models() -> None:
    phases: list[tuple[str, int]] = []

    async def fake_request(phase, model_type, **kwargs):
        phases.append((phase, kwargs["max_tokens"]))
        if model_type is profile.IdentityCoreSlice:
            return profile.IdentityCoreSlice(
                company_name="Example",
                business_summary="Engineering company",
                evidence=["Official site identifies the company"],
                company_facts=[profile.CompactCompanyFact(field="website", value="https://example.com", confidence="Высокая", source_ids=["S1"])],
                risks_and_assumptions=["Identity needs registry confirmation"],
            )
        if model_type is profile.ManagementSlice:
            return profile.ManagementSlice(company_facts=[profile.ManagementCompanyFact(field="executives", value="Named executive", source_ids=["E2"])])
        if model_type is profile.OwnershipNetworkSlice:
            return profile.OwnershipNetworkSlice(company_facts=[profile.OwnershipNetworkCompanyFact(field="affiliates", value="Related company hypothesis", source_ids=["E3"])])
        if model_type is profile.OperationsProfileSlice:
            return profile.OperationsProfileSlice(company_facts=[profile.CompactCompanyFact(field="products", value="Engineering services", source_ids=["E4"])])
        if model_type is profile.SignalProfileSlice:
            return profile.SignalProfileSlice(economic_signals=[profile.CompactEconomicSignal(signal="Market signal", evidence="Court mention", business_effect="Possible automation demand", source_ids=["E5"])])
        raise AssertionError(model_type)

    merged = asyncio.run(
        profile.extract_profile_parallel(
            request_json=fake_request,
            strict_request_json=fake_request,
            url="https://example.com",
            title="Example",
            text="Official site text",
            external_sources=[
                _source("E1", "contact"),
                _source("E2", "ownership"),
                _source("E3", "affiliation"),
                _source("E4", "other"),
                _source("E5", "court"),
            ],
            accessed_at="2026-08-16T00:00:00+00:00",
        )
    )

    assert {phase for phase, _ in phases} == {
        "profile_identity_core", "profile_management", "profile_ownership_network",
        "profile_operations", "profile_signals",
    }
    assert all(isinstance(item, CompanyFact) for item in merged.company_facts)
    assert {item.field for item in merged.company_facts} >= {"website", "executives", "affiliates", "products"}
    assert merged.economic_signals[0].source_ids == ["E5"]
    assert merged.coverage.complete is True
