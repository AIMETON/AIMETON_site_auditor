from __future__ import annotations

import re

from app.external_sources import source_type
from app.models import EvidenceBlock, EvidenceSource, IntelligenceSource


_CHILD_ID = re.compile(r"^(?P<parent>.+)-b(?P<block>\d+)-(?P<offset>\d+)$")
_TRIAGE_NOTE = re.compile(
    r"Evidence triage:\s*(?P<relation>[a-z_]+)/(?P<kind>[a-z_]+);\s*(?P<reason>[^.]+)\."
)


def evidence_parent_id(source_id: str) -> str:
    match = _CHILD_ID.match(source_id)
    return match.group("parent") if match else source_id


def collapse_source_ids(source_ids: list[str]) -> list[str]:
    """Remap transitional block ids to stable document ids without duplicates."""
    result: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        parent_id = evidence_parent_id(source_id)
        if parent_id in seen:
            continue
        seen.add(parent_id)
        result.append(parent_id)
    return result


def _block_from_child(source: IntelligenceSource) -> EvidenceBlock | None:
    if not source.evidence_quote or not source.evidence_locator or not source.evidence_digest:
        return None
    match = _CHILD_ID.match(source.id)
    if match is None:
        return None
    triage = _TRIAGE_NOTE.search(source.verification_note or "")
    relation = triage.group("relation") if triage else "target"
    reason = triage.group("reason") if triage else "retained_by_evidence_triage"
    if relation not in {
        "target", "affiliate", "counterparty", "competitor", "publisher",
        "mentioned_only", "unknown",
    }:
        relation = "unknown"
    return EvidenceBlock(
        id=f"B{match.group('block')}-{match.group('offset')}",
        evidence_quote=source.evidence_quote,
        evidence_locator=source.evidence_locator,
        evidence_digest=source.evidence_digest,
        query_kind=source.query_kind,
        relevance="high" if relation == "target" else "medium",
        entity_relation=relation,
        role="primary_fact" if relation == "target" else "context",
        confidence=0.9 if relation == "target" else 0.7,
        reason=reason,
    )


def collapse_verified_evidence(
    verified: list[IntelligenceSource],
) -> list[EvidenceSource]:
    """Project transitional block-as-source records into document-level UI evidence.

    RouterAI extraction may still receive child records for compatibility. This
    projection is the public/persistent boundary: one parent source card per fetched
    document, with retained locatable evidence nested in EvidenceBlock[].
    """
    parents: dict[str, IntelligenceSource] = {}
    children: dict[str, list[IntelligenceSource]] = {}
    order: list[str] = []

    for source in verified:
        parent_id = evidence_parent_id(source.id)
        if parent_id not in order:
            order.append(parent_id)
        if parent_id == source.id:
            parents[parent_id] = source
        else:
            children.setdefault(parent_id, []).append(source)
            parents.setdefault(parent_id, source)

    projected: list[EvidenceSource] = []
    for parent_id in order:
        source = parents[parent_id]
        if not source.evidence_quote:
            continue
        blocks: list[EvidenceBlock] = []
        seen_digests: set[str] = set()
        for child in children.get(parent_id, []):
            block = _block_from_child(child)
            if block is None or block.evidence_digest in seen_digests:
                continue
            seen_digests.add(block.evidence_digest)
            blocks.append(block)

        projected.append(
            EvidenceSource(
                id=parent_id,
                title=source.document_title or source.title,
                url=source.document_url or source.url,
                accessed_at=source.document_accessed_at or source.accessed_at,
                evidence_quote=source.evidence_quote,
                source_type=source_type(source.source_class),
                evidence_level=source.evidence_level,
                document_url=source.document_url,
                document_title=source.document_title,
                document_accessed_at=source.document_accessed_at,
                document_digest=source.document_digest,
                evidence_locator=source.evidence_locator,
                evidence_digest=source.evidence_digest,
                fetch_path=source.fetch_path,
                evidence_blocks=blocks,
            )
        )
    return projected


def merge_document_sources(
    existing: list[EvidenceSource],
    projected: list[EvidenceSource],
) -> list[EvidenceSource]:
    """Remove transitional child cards and merge document evidence by stable id."""
    result: list[EvidenceSource] = []
    positions: dict[str, int] = {}

    for source in existing:
        parent_id = evidence_parent_id(source.id)
        if parent_id != source.id:
            continue
        if parent_id in positions:
            continue
        positions[parent_id] = len(result)
        result.append(source)

    for source in projected:
        position = positions.get(source.id)
        if position is None:
            positions[source.id] = len(result)
            result.append(source)
            continue
        current = result[position]
        block_by_digest = {
            block.evidence_digest: block
            for block in current.evidence_blocks
        }
        for block in source.evidence_blocks:
            block_by_digest.setdefault(block.evidence_digest, block)
        result[position] = current.model_copy(update={
            "title": source.title or current.title,
            "url": source.url or current.url,
            "accessed_at": source.accessed_at or current.accessed_at,
            "evidence_quote": source.evidence_quote or current.evidence_quote,
            "source_type": source.source_type,
            "evidence_level": source.evidence_level,
            "document_url": source.document_url or current.document_url,
            "document_title": source.document_title or current.document_title,
            "document_accessed_at": source.document_accessed_at or current.document_accessed_at,
            "document_digest": source.document_digest or current.document_digest,
            "evidence_locator": source.evidence_locator or current.evidence_locator,
            "evidence_digest": source.evidence_digest or current.evidence_digest,
            "fetch_path": source.fetch_path or current.fetch_path,
            "evidence_blocks": list(block_by_digest.values()),
        })

    return result
