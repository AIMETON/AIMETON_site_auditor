from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from pydantic import BaseModel, Field

from app.models import IntelligenceSource, SourceKind
from app.search_gateway.gateway import canonical_url


_BLOCK_CHILD_RE = re.compile(r"^(?P<parent>.+)-b(?P<index>\d+)-(?P<offset>\d+)$")


class EvidenceBlock(BaseModel):
    """A retained, locatable evidence fragment belonging to one primary document."""

    id: str
    source_id: str
    query_kind: SourceKind
    quote: str
    locator: str | None = None
    digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    block_index: int | None = Field(default=None, ge=0)
    offset: int | None = Field(default=None, ge=0)


@dataclass
class EvidenceLedger:
    """Document-level source ledger plus nested evidence blocks for extraction."""

    documents: list[IntelligenceSource] = field(default_factory=list)
    blocks_by_source: dict[str, list[EvidenceBlock]] = field(default_factory=dict)
    raw_evidence_records: int = 0
    child_records_compacted: int = 0
    duplicate_documents_removed: int = 0
    duplicate_blocks_removed: int = 0

    @property
    def block_count(self) -> int:
        return sum(len(items) for items in self.blocks_by_source.values())

    def extraction_records(self) -> list[dict]:
        """Expand nested blocks only at the internal extraction boundary.

        Every block keeps the parent document id as `id`, so facts/signals reference a
        real document source rather than a synthetic DOM-fragment source. The block id
        remains available as provenance metadata.
        """
        from app.external_sources import to_llm_sources

        records: list[dict] = []
        for document in self.documents:
            base = to_llm_sources([document])[0]
            blocks = self.blocks_by_source.get(document.id, [])
            if not blocks:
                records.append(base)
                continue
            for block in blocks:
                record = dict(base)
                record.update({
                    "id": document.id,
                    "parent_source_id": document.id,
                    "evidence_block_id": block.id,
                    "query_kind": block.query_kind,
                    "snippet": block.quote,
                    "evidence_quote": block.quote,
                    "evidence_locator": block.locator,
                    "evidence_digest": block.digest,
                })
                records.append(record)
        return records


def _child_parts(source_id: str) -> tuple[str, int, int] | None:
    match = _BLOCK_CHILD_RE.match(source_id)
    if not match:
        return None
    return match.group("parent"), int(match.group("index")), int(match.group("offset"))


def is_block_child(source: IntelligenceSource) -> bool:
    return _child_parts(source.id) is not None


def _document_key(source: IntelligenceSource) -> str:
    if source.document_digest:
        return f"digest:{source.document_digest}"
    url = source.document_url or source.url
    return f"url:{canonical_url(str(url))}"


def _block_key(block: EvidenceBlock) -> tuple[str, str, str]:
    if block.digest:
        return ("digest", block.digest, block.query_kind)
    return (block.locator or "", " ".join(block.quote.split()), block.query_kind)


def build_evidence_ledger(records: Iterable[IntelligenceSource]) -> EvidenceLedger:
    """Compact transitional block-as-source records into an explicit nested ledger.

    The verifier currently emits a parent record plus `-b<index>-<offset>` children for
    compatibility. This boundary removes that representation before expensive extraction
    and before public result assembly. Documents are additionally deduplicated by fetched
    content digest, falling back to canonical final URL.
    """
    raw = list(records)
    parents: dict[str, IntelligenceSource] = {}
    child_rows: list[tuple[IntelligenceSource, str, int, int]] = []

    for source in raw:
        parts = _child_parts(source.id)
        if parts is None:
            parents.setdefault(source.id, source)
            continue
        parent_id, block_index, offset = parts
        child_rows.append((source, parent_id, block_index, offset))
        if parent_id not in parents:
            parents[parent_id] = source.model_copy(update={"id": parent_id}, deep=True)

    canonical_by_doc_key: dict[str, IntelligenceSource] = {}
    source_alias: dict[str, str] = {}
    documents: list[IntelligenceSource] = []
    duplicate_documents = 0
    for parent in parents.values():
        key = _document_key(parent)
        canonical = canonical_by_doc_key.get(key)
        if canonical is None:
            canonical_by_doc_key[key] = parent
            documents.append(parent)
            source_alias[parent.id] = parent.id
        else:
            source_alias[parent.id] = canonical.id
            duplicate_documents += 1

    blocks_by_source: dict[str, list[EvidenceBlock]] = {item.id: [] for item in documents}
    block_keys: dict[str, set[tuple[str, str, str]]] = {item.id: set() for item in documents}
    duplicate_blocks = 0
    for child, parent_id, block_index, offset in child_rows:
        canonical_id = source_alias.get(parent_id, parent_id)
        if canonical_id not in blocks_by_source:
            continue
        block = EvidenceBlock(
            id=child.id,
            source_id=canonical_id,
            query_kind=child.query_kind,
            quote=child.evidence_quote or child.snippet,
            locator=child.evidence_locator,
            digest=child.evidence_digest,
            block_index=block_index,
            offset=offset,
        )
        key = _block_key(block)
        if key in block_keys[canonical_id]:
            duplicate_blocks += 1
            continue
        block_keys[canonical_id].add(key)
        blocks_by_source[canonical_id].append(block)

    for items in blocks_by_source.values():
        items.sort(key=lambda item: (
            item.block_index if item.block_index is not None else 10**9,
            item.offset if item.offset is not None else 10**9,
            item.id,
        ))

    return EvidenceLedger(
        documents=documents,
        blocks_by_source=blocks_by_source,
        raw_evidence_records=len(raw),
        child_records_compacted=len(child_rows),
        duplicate_documents_removed=duplicate_documents,
        duplicate_blocks_removed=duplicate_blocks,
    )


def compact_source_list_in_place(sources: list[IntelligenceSource]) -> int:
    """Keep one public source per verified primary document.

    Transitional block children are removed. Verified parents are also collapsed by
    fetched content digest/canonical final URL, while non-evidence discovery candidates
    remain visible for lifecycle diagnostics.
    """
    before = len(sources)
    compacted: list[IntelligenceSource] = []
    seen_evidence_documents: set[str] = set()
    for source in sources:
        if is_block_child(source):
            continue
        if source.lifecycle_state == "evidence":
            key = _document_key(source)
            if key in seen_evidence_documents:
                continue
            seen_evidence_documents.add(key)
        compacted.append(source)
    sources[:] = compacted
    return before - len(sources)
