from app.evidence_source_projection import (
    collapse_source_ids,
    collapse_verified_evidence,
    evidence_parent_id,
    merge_document_sources,
)
from app.models import EvidenceSource, IntelligenceSource


def _source(identifier: str, *, quote: str, digest: str, kind: str = "registry") -> IntelligenceSource:
    return IntelligenceSource(
        id=identifier,
        title="Registry card",
        url="https://registry.example/company",
        accessed_at="2026-09-17T00:00:00Z",
        source_class="registry",
        query_kind=kind,
        lifecycle_state="evidence",
        evidence_level="corroborated_signal",
        document_url="https://registry.example/company",
        document_title="Registry card",
        document_accessed_at="2026-09-17T00:00:00Z",
        document_digest="sha256:" + "a" * 64,
        evidence_quote=quote,
        evidence_locator="body/main",
        evidence_digest=digest,
        fetch_path="static",
        verification_note=(
            "Первичный документ загружен. Evidence triage: target/"
            f"{kind}; retained target block."
        ),
    )


def _public(identifier: str) -> EvidenceSource:
    return EvidenceSource(
        id=identifier,
        title="Existing",
        url="https://registry.example/company",
        accessed_at="2026-09-17T00:00:00Z",
        evidence_quote="existing quote",
        source_type="registry",
        evidence_level="corroborated_signal",
    )


def test_child_ids_collapse_to_one_document_with_nested_blocks():
    parent = _source("H1", quote="identity anchor", digest="sha256:" + "1" * 64)
    first = _source("H1-b2-0", quote="ИНН 2462215501", digest="sha256:" + "2" * 64)
    second = _source("H1-b5-0", quote="Директор Иванов", digest="sha256:" + "3" * 64, kind="ownership")

    projected = collapse_verified_evidence([parent, first, second])

    assert len(projected) == 1
    assert projected[0].id == "H1"
    assert [block.id for block in projected[0].evidence_blocks] == ["B2-0", "B5-0"]
    assert [block.query_kind for block in projected[0].evidence_blocks] == ["registry", "ownership"]
    assert all(block.entity_relation == "target" for block in projected[0].evidence_blocks)
    assert not any("-b" in source.id for source in projected)


def test_duplicate_block_digest_is_not_rendered_twice():
    parent = _source("H7", quote="anchor", digest="sha256:" + "4" * 64)
    one = _source("H7-b1-0", quote="same", digest="sha256:" + "5" * 64)
    duplicate = _source("H7-b3-0", quote="same", digest="sha256:" + "5" * 64)

    projected = collapse_verified_evidence([parent, one, duplicate])

    assert len(projected) == 1
    assert len(projected[0].evidence_blocks) == 1
    assert projected[0].evidence_blocks[0].evidence_quote == "same"


def test_public_ledger_drops_existing_child_cards_and_keeps_other_sources():
    parent = _source("H1", quote="anchor", digest="sha256:" + "6" * 64)
    child = _source("H1-b4-0", quote="retained", digest="sha256:" + "7" * 64)
    projected = collapse_verified_evidence([parent, child])

    merged = merge_document_sources(
        [_public("S1"), _public("H1-b4-0"), _public("H1")],
        projected,
    )

    assert [source.id for source in merged] == ["S1", "H1"]
    assert len(merged[1].evidence_blocks) == 1
    assert merged[1].evidence_blocks[0].evidence_quote == "retained"


def test_source_references_remap_children_to_stable_parent_ids():
    assert collapse_source_ids(["S1", "H1-b2-0", "H1-b5-4000", "H1", "S1"]) == ["S1", "H1"]


def test_parent_id_only_strips_transitional_block_suffix():
    assert evidence_parent_id("H1-b17-4000") == "H1"
    assert evidence_parent_id("R-H2") == "R-H2"
    assert evidence_parent_id("company-b-name") == "company-b-name"
