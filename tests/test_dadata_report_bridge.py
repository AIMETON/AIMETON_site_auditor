from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.dadata_report_bridge import enrich_identity_with_dadata, enrich_identifier_candidates_with_dadata
from app.entity_resolution.dadata import (
    DaDataLookupResult,
    DaDataPartyRecord,
    RegistryMirrorState,
)
from app.external_sources import IdentityAnchors


def _record() -> DaDataPartyRecord:
    return DaDataPartyRecord(
        id="dadata_party_test",
        accessed_at=datetime.now(UTC),
        response_digest="sha256:" + "a" * 64,
        query="7707083893",
        legal_name="ПАО ТЕСТ",
        short_name="ПАО ТЕСТ",
        inn="7707083893",
        kpp="773601001",
        ogrn="1027700132195",
        entity_type="LEGAL",
        branch_type="MAIN",
        status="ACTIVE",
        actuality_date=1_785_283_200_000,
        raw_hid="hid-test",
    )


@pytest.mark.asyncio
async def test_bridge_skips_provider_without_identifier(monkeypatch):
    def forbidden_provider():
        raise AssertionError("DaData must not be called without INN/OGRN")
    monkeypatch.setattr("app.dadata_report_bridge.get_dadata_registry_mirror_provider", forbidden_provider)
    anchors = IdentityAnchors(domain="example.org", cities=("Красноярск",))
    updated, result, facts, notes = await enrich_identity_with_dadata(anchors)
    assert updated == anchors
    assert result is None
    assert facts == []
    assert any("not_attempted" in note for note in notes)


@pytest.mark.asyncio
async def test_verified_mirror_normalizes_anchors_but_never_claims_authority(monkeypatch):
    class FakeProvider:
        def lookup(self, query: str):
            assert query == "7707083893"
            return DaDataLookupResult(state=RegistryMirrorState.VERIFIED, query=query, records=[_record()], authority_verified=False)
    monkeypatch.setattr("app.dadata_report_bridge.get_dadata_registry_mirror_provider", lambda: FakeProvider())
    anchors = IdentityAnchors(domain="example.org", inn="7707083893", cities=("Красноярск",))
    updated, result, facts, notes = await enrich_identity_with_dadata(anchors)
    assert result is not None
    assert result.state is RegistryMirrorState.VERIFIED
    assert result.authority_verified is False
    assert updated.inn == "7707083893"
    assert updated.ogrn == "1027700132195"
    assert updated.legal_name == "ПАО ТЕСТ"
    assert {fact.field for fact in facts} >= {"legal_name", "inn", "ogrn", "registration_status"}
    assert all("authority_verified=false" in fact.note for fact in facts)
    assert any("официальная верификация ФНС" in note for note in notes)


@pytest.mark.asyncio
async def test_conflicting_mirror_is_visible_but_does_not_replace_identity(monkeypatch):
    record = _record()
    class FakeProvider:
        def lookup(self, query: str):
            return DaDataLookupResult(state=RegistryMirrorState.CONFLICTING, query=query, records=[record], conflicts=["multiple_registry_mirror_records"], authority_verified=False)
    monkeypatch.setattr("app.dadata_report_bridge.get_dadata_registry_mirror_provider", lambda: FakeProvider())
    anchors = IdentityAnchors(domain="example.org", legal_name="ООО ИСХОДНОЕ", inn="7707083893", cities=("Красноярск",))
    updated, result, facts, notes = await enrich_identity_with_dadata(anchors)
    assert result is not None
    assert result.state is RegistryMirrorState.CONFLICTING
    assert updated == anchors
    assert facts
    assert any("конфликтующие/множественные записи" in note for note in notes)



@pytest.mark.asyncio
async def test_multi_candidate_resolution_checks_all_and_selects_unique_target(monkeypatch):
    target = _record().model_copy(
        update={"legal_name": 'ООО "АЛЬФА ДЕНТ"', "short_name": "АЛЬФА ДЕНТ"}
    )
    other = DaDataPartyRecord(
        id="dadata_party_other",
        accessed_at=datetime.now(UTC),
        response_digest="sha256:" + "b" * 64,
        query="7811111111",
        legal_name='ООО "ПАРТНЕР СЕРВИС"',
        short_name="ПАРТНЕР СЕРВИС",
        inn="7811111111",
        kpp="781101001",
        ogrn="1027800000000",
        entity_type="LEGAL",
        branch_type="MAIN",
        status="ACTIVE",
        actuality_date=1_785_283_200_000,
        raw_hid="hid-other",
    )
    calls = []
    class FakeProvider:
        def lookup(self, query: str):
            calls.append(query)
            record = target if query == "7707083893" else other
            return DaDataLookupResult(
                state=RegistryMirrorState.VERIFIED,
                query=query,
                records=[record],
                authority_verified=False,
            )
    monkeypatch.setattr(
        "app.dadata_report_bridge.get_dadata_registry_mirror_provider",
        lambda: FakeProvider(),
    )
    anchors = IdentityAnchors(domain="example.org")
    updated, result, facts, notes, checked = await enrich_identifier_candidates_with_dadata(
        anchors,
        [
            ("inn", "7707083893", True),
            ("inn", "7811111111", False),
        ],
        company_hint="Альфа Дент — стоматология",
    )
    assert checked == 2
    assert calls == ["7707083893", "7811111111"]
    assert result is not None
    assert updated.inn == "7707083893"
    assert updated.legal_name == 'ООО "АЛЬФА ДЕНТ"'
    assert {fact.value for fact in facts} >= {"7707083893", 'ООО "АЛЬФА ДЕНТ"'}
    assert any("target candidate selected" in note for note in notes)


@pytest.mark.asyncio
async def test_multi_candidate_resolution_keeps_identity_provisional_on_tie(monkeypatch):
    def make_record(query: str, suffix: str) -> DaDataPartyRecord:
        return DaDataPartyRecord(
            id=f"dadata_party_{suffix}",
            accessed_at=datetime.now(UTC),
            response_digest="sha256:" + suffix * 64,
            query=query,
            legal_name=f'ООО "АЛЬФА {suffix.upper()}"',
            short_name=f"АЛЬФА {suffix.upper()}",
            inn=query,
            kpp=None,
            ogrn=None,
            entity_type="LEGAL",
            branch_type="MAIN",
            status="ACTIVE",
            actuality_date=1_785_283_200_000,
            raw_hid=f"hid-{suffix}",
        )
    records = {
        "7707083893": make_record("7707083893", "a"),
        "7811111111": make_record("7811111111", "b"),
    }
    class FakeProvider:
        def lookup(self, query: str):
            return DaDataLookupResult(
                state=RegistryMirrorState.VERIFIED,
                query=query,
                records=[records[query]],
                authority_verified=False,
            )
    monkeypatch.setattr(
        "app.dadata_report_bridge.get_dadata_registry_mirror_provider",
        lambda: FakeProvider(),
    )
    anchors = IdentityAnchors(domain="example.org")
    updated, result, facts, notes, checked = await enrich_identifier_candidates_with_dadata(
        anchors,
        [
            ("inn", "7707083893", False),
            ("inn", "7811111111", False),
        ],
        company_hint="Альфа",
    )
    assert checked == 2
    assert updated == anchors
    assert result is None
    assert facts == []
    assert any("ambiguous" in note for note in notes)
