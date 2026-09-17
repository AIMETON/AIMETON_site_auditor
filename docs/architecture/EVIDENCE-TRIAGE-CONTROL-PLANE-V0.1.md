# Evidence Triage Control Plane v0.1

Status: implementation started 2026-09-17 after the Aleks Dent deep-research regression.

## Problem

Deep research currently treats breadth as coverage. A fetched document may be promoted when a target identifier appears anywhere in the page, `preserve_blocks` can turn every DOM block into an `IntelligenceSource`, and verified lifecycle state can bypass semantic vertical routing. The observed result is an evidence fan-out where tens of real documents become thousands of source records and hundreds of expensive extraction calls. Foreign company cards, directory publisher footers and related-company sections can contaminate the target dossier.

Unlimited research budget must authorize depth, not disable relevance selection.

## Target pipeline

```text
official site
  -> resolved identity
  -> search wave
  -> fast search-result triage (O1)
  -> primary URL fetch
  -> deterministic cleanup / identity guard
  -> fast document preflight (O1)
  -> structural block building
  -> deterministic block guards
  -> fast block triage + semantic routing (O1)
  -> verified evidence units
  -> vertical extraction
  -> semantic consolidation
  -> compact company dossier
  -> expensive reasoning
  -> client report
```

The full fetched document/evidence ledger may remain durable for auditability. Expensive extraction and final reasoning do not receive the raw ledger without triage.

## Fast model role

The fast model is a control-plane classifier/router. It is intentionally separate from expensive company extraction and commercial reasoning.

Default profile: `routerai-qwen35-9b`, resolved through the existing governed Search Observer model-profile registry and the existing RouterAI credential. Override with `AIMETON_FAST_RESEARCH_MODEL_PROFILE`.

Fast calls use:

- structured JSON Schema output;
- temperature 0;
- reasoning disabled;
- bounded 2-30 second timeout, independent from deep-research long reasoning timeouts;
- existing research accounting (`record_llm_start` / `record_llm_usage`);
- batched inputs rather than one call per block/result.

The fast model may:

- prioritize which discovery hints deserve a primary fetch;
- classify target / affiliate / publisher / competitor / mentioned-only relationships;
- label relevance;
- route a retained block to a semantic query kind;
- reject obvious content noise.

It may not:

- promote a snippet to evidence;
- establish canonical INN/OGRN by itself;
- override deterministic identity guards;
- change SearchGateway provider/routing/budget/circuit/concurrency policy;
- extract final company facts;
- calculate commercial score;
- produce the final dossier or recommendation.

## Stage A: search-result triage

Input is bounded metadata only: source id, title, URL, snippet, query kind and source class plus resolved target anchors.

Output is `fetch` or `skip`, relation, semantic query kind, confidence and short reason.

Search snippets remain `discovery_hint`. A `fetch` decision only authorizes document acquisition. The fetched body must still pass primary-entity verification before evidence promotion.

Deterministic selection runs before the model:

- official domain is fetch-worthy;
- a strong target identifier in the result is fetch-worthy;
- canonical target name on a relevant vertical is fetch-worthy;
- obvious generic unknown results may be skipped;
- ambiguous results go to the fast model in batches.

If the fast model is unavailable, the system does not expand the third-party evidence frontier merely to preserve recall. Deterministically selected candidates continue; ambiguous candidates remain unfetched. First-party acquisition is preserved independently.

## Stage B: primary-entity verification

A target INN/OGRN found somewhere in a foreign page is no longer sufficient.

Strong identifiers must occur in primary document context. Related-company sections, similar-company lists, sidebar/aside content and publisher/footer containers are explicitly non-primary contexts. Company/title context and structural location are considered around the identifier.

First-party documents on the already resolved official domain remain trusted.

This guard runs before block triage and is authoritative over the fast model.

## Stage C: document preflight

Large-document relevance preflight uses the same O1 fast-model path instead of the expensive extraction model. It samples outline/beginning/middle/end/company mentions and returns include/exclude/uncertain.

Preflight does not convert sampled text into evidence.

## Stage D: block triage

Blocks are classified before expensive extraction.

Deterministic guards run first:

- navigation/cookie/script/style containers -> noise;
- related/similar/other-company sections -> mentioned/related entity, reject;
- third-party footer/aside/sidebar -> publisher metadata, reject;
- target INN/OGRN/known phone in a clean block -> high target relevance;
- canonical target name -> target relevance;
- first-party non-noise blocks -> retain conservatively.

Only ambiguous third-party blocks are sent to the O1 model. Batches contain up to 32 block previews and a bounded character envelope.

The model emits:

- `keep`;
- relevance;
- entity relation;
- semantic `query_kind`;
- evidence role;
- confidence;
- reason.

A deterministic post-validator can only narrow model output. Publisher, competitor, counterparty, mentioned-only and unknown relations cannot be promoted as target evidence. Affiliate evidence is retained only for ownership/affiliation routing.

On O1 failure, ambiguous third-party blocks are rejected rather than passed wholesale to expensive extraction.

## Semantic routing invariant

`lifecycle_state=evidence` means the content was verified. It does not mean the content is relevant to every extractor.

`project_sources` must always require semantic `query_kind` membership in the vertical's allowed kinds. Evidence trust never bypasses routing.

Block triage may refine `query_kind` based on block content. This prevents registry/review/catalog text from being duplicated into unrelated extraction verticals.

## Current transitional representation

v0.1 still serializes retained block evidence as child `IntelligenceSource` records for compatibility with existing RouterAI extraction and UI schemas. The number of child records is now constrained by semantic selection rather than raw DOM size.

A follow-up version must split this representation explicitly:

```text
IntelligenceSource (one primary document / URL)
  -> EvidenceBlock[] (locatable retained blocks)
```

The UI/source ledger should render documents as sources and blocks as nested evidence, not thousands of independent source cards.

## Failure semantics

- Search triage failure: preserve first-party and deterministic high-confidence candidates; do not fetch every ambiguous result.
- Document preflight failure: return `uncertain`; do not claim exclusion.
- Block triage failure: keep already deterministic first-party/high-confidence evidence, reject ambiguous third-party blocks.
- Identity guard failure: never promote the document.
- Expensive extraction failure: must not retroactively convert rejected raw content into evidence.

No fallback is allowed to mean "send the whole raw corpus to the expensive model".

## Telemetry contract

Add/retain counters for:

- search results raw/triaged/selected/rejected;
- documents attempted/fetched/identity-rejected;
- raw document blocks;
- deterministically accepted/rejected blocks;
- O1-triaged blocks;
- retained evidence blocks;
- O1 calls/tokens;
- extraction calls/tokens;
- reasoning calls/tokens;
- unique documents vs evidence child records;
- duplicate ratio;
- foreign-entity facts rejected;
- final reasoning state.

The key health ratios are:

```text
search_selection_ratio = selected_results / raw_results
block_selection_ratio = retained_blocks / raw_blocks
source_fanout_ratio = evidence_child_records / unique_documents
```

A large fan-out is a diagnostic signal, not a quality achievement.

## Regression contract

Required cases:

1. target INN appears only in a related-company section of a foreign company page -> document rejected;
2. directory publisher footer such as `ООО ЗУН` -> block rejected;
3. target registry card with target INN -> retained;
4. verified registry evidence does not enter unrelated `other` vertical automatically;
5. one discovery result remains `discovery_hint` after fast search triage until fetched;
6. fast model failure does not expand ambiguous third-party evidence;
7. first-party evidence remains available when fast model is unavailable;
8. large-document preflight uses the fast profile, not expensive reasoning model.

## Next implementation slices

1. Replace transitional block-as-source children with explicit `EvidenceBlock` storage and nested UI rendering.
2. Add progressive coverage-driven search waves; the fast model should request new directions only for actual coverage gaps.
3. Add URL/final-URL/content-digest dedup before verification.
4. Add semantic evidence batching before extraction.
5. Add post-extraction entity consistency and semantic consolidation.
6. Build compact `CompanyDossier`; final expensive reasoning consumes only the dossier plus source references.
7. Remove heuristic commercial score substitution on reasoning failure.
8. Move full analysis history server-side and keep browser history lightweight.

## Acceptance target for the Aleks Dent regression

The repaired pipeline must show all of the following on the same target:

- no foreign legal entity from directory publisher/related-company content appears as target founder/executive;
- verified services, contacts, locations, doctors/licences/requisites survive;
- rendered sources are close to real document count rather than raw DOM block count;
- expensive extraction calls and tokens fall by at least an order of magnitude from the broken run unless new verified coverage justifies additional work;
- final reasoning completes successfully or explicitly returns not-computed, never a misleading heuristic `100/100`.
