# Evidence Block Storage v0.1

Status: implementation slice following the merged Evidence Triage Control Plane (#931).

## Invariant

A primary fetched document is one public evidence source. Retained structural evidence is nested under that document.

```text
EvidenceSource (one primary document / URL)
  -> EvidenceBlock[] (retained locatable evidence)
```

A DOM block or 4k fragment must not become an independent source card in `SiteAnalysis.sources`.

## Transitional extraction boundary

RouterAI extraction may temporarily receive block child records such as `H1-b4-0` while the internal extraction schema is migrated. This representation is private to the extraction boundary.

Before a result is persisted or rendered:

1. child records are grouped by stable parent document id;
2. duplicate evidence digests are removed;
3. locators, digests, semantic query kinds and retained quotes are stored as `EvidenceBlock[]`;
4. public source cards use the parent id only;
5. `CompanyFact`, `EconomicSignal` and Business Machine source references are remapped from child ids to the parent document id.

This prevents a fact from retaining a dangling `H1-b...` reference after public source collapse.

## Public model

`EvidenceSource.evidence_blocks` is additive and defaults to an empty list, preserving compatibility with results that contain only a document-level anchor quote.

Each `EvidenceBlock` carries:

- stable block id inside the parent;
- verbatim retained quote;
- locator and SHA-256 evidence digest;
- semantic query kind;
- target/entity relation;
- relevance and evidence role;
- triage confidence and bounded reason.

## UI contract

UI counters distinguish primary documents from retained evidence blocks. Source lists render one card per document. Evidence blocks are expandable details under the parent document rather than peer source cards.

The number of source cards therefore measures documents, not DOM fragments.

## Failure and compatibility

- Existing `EvidenceSource` payloads without nested blocks remain valid.
- Existing RouterAI extraction can continue consuming transitional child records during this slice.
- Public normalization only narrows source fan-out; it does not promote rejected evidence.
- Deduplication by block evidence digest affects rendering/storage only and does not invent or merge fact content.

## Regression contract

1. `H1`, `H1-b2-0`, `H1-b5-0` become one public source `H1` with two nested evidence blocks.
2. Duplicate block digests are rendered once.
3. Existing child cards from RouterAI assembly are removed at the public boundary.
4. `source_ids=[H1-b2-0, H1-b5-0, H1]` becomes `source_ids=[H1]`.
5. Non-child sources such as `S1` remain unchanged.

## Next slices

- remove transitional child records from RouterAI extraction itself by teaching extraction to consume nested evidence batches directly;
- deduplicate fetched content by canonical/final URL plus document digest;
- drive additional search waves from actual evidence coverage gaps;
- add semantic consolidation/entity consistency before compact dossier reasoning;
- keep the full analysis server-side and browser history lightweight.
