# Post-fetch Document Dedup v0.1

Part of the audit-quality recovery after #931–#933.

## Boundary

Discovery dedup can only see search URLs. Redirects, canonical final URLs and normalized content digests become known after primary-document verification. Before expensive RouterAI profile extraction, verified evidence is therefore grouped by fetched document and deduplicated again.

## Invariants

- duplicate canonical final URLs are represented once in the extraction corpus;
- identical normalized document digests are represented once even when URLs differ (mirror/cache copies);
- unique retained semantic evidence blocks from a duplicate group are not discarded: they are re-parented onto the surviving document id;
- duplicate block evidence digests are not repeated;
- dedup occurs before `to_llm_sources()` and therefore before expensive profile extraction;
- public source projection and fact provenance continue to use the surviving stable document id;
- no search snippet is promoted and no rejected document is reintroduced.

## Telemetry

`research_status.postfetch_duplicate_documents` records the number of fetched document groups removed before expensive extraction. The existing `transitional_extraction_records` counter reflects the deduplicated corpus passed onward.

## Deliberate scope

This slice removes duplicate evidence from expensive extraction. The crawler may still fetch/cheap-triage two search candidates that ultimately resolve to the same document. A later cache layer may reuse preflight/block-triage decisions by digest, but that optimization must not alter entity verification or evidence admission semantics.
