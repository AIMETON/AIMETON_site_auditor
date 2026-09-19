from app.identity_readiness import assess_identity_readiness, identity_release_blocker
from app.models import CompanyFact, EvidenceSource


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _source(source_id: str, level: str, *, traceable: bool = True) -> EvidenceSource:
    return EvidenceSource(
        id=source_id,
        title=source_id,
        url=f"https://example.test/{source_id}",
        accessed_at="2026-09-19T00:00:00+00:00",
        evidence_quote=f"Evidence {source_id}",
        evidence_level=level,
        document_url=f"https://example.test/{source_id}",
        document_digest=_digest("a") if traceable else None,
        evidence_locator="body/p[1]" if traceable else None,
        evidence_digest=_digest("b") if traceable else None,
    )


def test_identity_resolved_requires_sourced_name_and_authoritative_identifier() -> None:
    facts = [
        CompanyFact(field="brand_name", value="Алекс Дент", source_ids=["S1"]),
        CompanyFact(field="inn", value="2462215501", source_ids=["R1"]),
    ]

    result = assess_identity_readiness(
        facts,
        sources=[_source("R1", "corroborated_signal")],
    )

    assert result.state == "resolved"
    assert result.authoritative_identifiers["inn"] == ("2462215501",)
    assert result.conflict_fields == ()
    assert identity_release_blocker(result.state) is None


def test_weak_identifier_keeps_identity_provisional() -> None:
    facts = [
        CompanyFact(field="legal_name", value='ООО "АЛЕКС ДЕНТ"', source_ids=["S1"]),
        CompanyFact(field="inn", value="2462215501", source_ids=["D1"]),
    ]

    result = assess_identity_readiness(
        facts,
        sources=[_source("D1", "weak_signal")],
    )

    assert result.state == "provisional"
    assert result.authoritative_identifiers["inn"] == ()
    assert identity_release_blocker(result.state) == "identity_provisional"


def test_conflicting_authoritative_inn_is_explicit_critical_conflict() -> None:
    facts = [
        CompanyFact(field="brand_name", value="Алекс Дент", source_ids=["S1"]),
        CompanyFact(field="inn", value="2462215501", source_ids=["R1"]),
        CompanyFact(field="inn", value="2462215502", source_ids=["R2"]),
    ]

    result = assess_identity_readiness(
        facts,
        sources=[
            _source("R1", "corroborated_signal"),
            _source("R2", "confirmed_fact"),
        ],
    )

    assert result.state == "conflicting"
    assert result.authoritative_identifiers["inn"] == ("2462215501", "2462215502")
    assert result.conflict_fields == ("inn",)
    assert result.unresolved_critical_conflicts == 1
    assert identity_release_blocker(result.state) == "identity_conflicting"


def test_untraceable_external_identifier_cannot_resolve_identity() -> None:
    facts = [
        CompanyFact(field="brand_name", value="Алекс Дент", source_ids=["S1"]),
        CompanyFact(field="ogrn", value="1112468013030", source_ids=["R1"]),
    ]

    result = assess_identity_readiness(
        facts,
        sources=[_source("R1", "confirmed_fact", traceable=False)],
    )

    assert result.state == "provisional"
    assert result.authoritative_identifiers["ogrn"] == ()


def test_unsourced_registry_mirror_facts_do_not_resolve_identity() -> None:
    facts = [
        CompanyFact(field="legal_name", value='ООО "АЛЕКС ДЕНТ"'),
        CompanyFact(field="inn", value="2462215501"),
    ]

    result = assess_identity_readiness(facts)

    assert result.state == "unresolved"
    assert result.sourced_names == ()
    assert identity_release_blocker(result.state) == "identity_unresolved"
