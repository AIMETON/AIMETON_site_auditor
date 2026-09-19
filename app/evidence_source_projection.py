from __future__ import annotations

from app.evidence_freshness import assess_source_freshness
import re

from app.external_sources import source_type
from app.models import EvidenceBlock, EvidenceSource, IntelligenceSource
from app.search_gateway.gateway import canonical_url


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


def _document_groups(
    verified: list[IntelligenceSource],
) -> tuple[list[str], dict[str, list[IntelligenceSource]]]:
    order: list[str] = []
    groups: dict[str, list[IntelligenceSource]] = {}
    for source in verified:
        parent_id = evidence_parent_id(source.id)
        if parent_id not in groups:
            groups[parent_id] = []
            order.append(parent_id)
        groups[parent_id].append(source)
    return order, groups


def _next_child_index(records: list[IntelligenceSource], parent_id: str) -> int:
    indexes = []
    for item in records:
        match = _CHILD_ID.match(item.id)
        if match and match.group("parent") == parent_id:
            indexes.append(int(match.group("block")))
    return max(indexes, default=-1) + 1


def deduplicate_verified_documents(
    verified: list[IntelligenceSource],
) -> tuple[list[IntelligenceSource], int]:
    """Collapse duplicate fetched documents before expensive profile extraction.

    Discovery canonicalization cannot see redirects or mirrored content. At this
    boundary every record has fetched-document metadata, so document groups are
    deduplicated by canonical final URL OR normalized document digest. Unique block
    evidence from a duplicate group is re-parented onto the surviving document.
    """
    order, groups = _document_groups(verified)
    survivor_by_url: dict[str, str] = {}
    survivor_by_digest: dict[str, str] = {}
    kept_order: list[str] = []
    kept_groups: dict[str, list[IntelligenceSource]] = {}
    block_digests: dict[str, set[str]] = {}
    used_ids: dict[str, set[str]] = {}
    next_child_index: dict[str, int] = {}
    duplicate_documents = 0

    for parent_id in order:
        records = groups[parent_id]
        parent = next((item for item in records if item.id == parent_id), records[0])
        final_url = canonical_url(str(parent.document_url or parent.url))
        digest = str(parent.document_digest or "")
        survivor = survivor_by_url.get(final_url) or (survivor_by_digest.get(digest) if digest else None)

        if survivor is None:
            kept_order.append(parent_id)
            kept_groups[parent_id] = list(records)
            block_digests[parent_id] = {
                item.evidence_digest
                for item in records
                if item.id != parent_id and item.evidence_digest
            }
            used_ids[parent_id] = {item.id for item in records}
            next_child_index[parent_id] = _next_child_index(records, parent_id)
            survivor_by_url[final_url] = parent_id
            if digest:
                survivor_by_digest[digest] = parent_id
            continue

        duplicate_documents += 1
        survivor_records = kept_groups[survivor]
        known_digests = block_digests[survivor]
        survivor_ids = used_ids[survivor]
        for item in records:
            if item.id == parent_id or not item.evidence_digest or item.evidence_digest in known_digests:
                continue
            match = _CHILD_ID.match(item.id)
            if match is None:
                continue
            clone = item.model_copy(deep=True)
            proposed_id = f"{survivor}-b{match.group('block')}-{match.group('offset')}"
            if proposed_id in survivor_ids:
                block_index = next_child_index[survivor]
                proposed_id = f"{survivor}-b{block_index}-0"
                while proposed_id in survivor_ids:
                    block_index += 1
                    proposed_id = f"{survivor}-b{block_index}-0"
                next_child_index[survivor] = block_index + 1
            clone.id = proposed_id
            survivor_records.append(clone)
            survivor_ids.add(proposed_id)
            known_digests.add(item.evidence_digest)

    result: list[IntelligenceSource] = []
    for parent_id in kept_order:
        result.extend(kept_groups[parent_id])
    return result, duplicate_documents


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
                published_at=source.published_at,
                freshness=assess_source_freshness(source.published_at),
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
            "published_at": source.published_at or current.published_at,
            "freshness": source.freshness if source.freshness != "unassessed" else current.freshness,
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
