# Audit Analysis Method v0.2

Checkpoint: 2026-09-19.
Base: `23ddaf5c3d448f8c5fef4084236eab0111f2a2a5`.
Related: #915, PRs #944–#955.

## Purpose

The auditor should behave like a careful evidence-first analyst, not like a crawler
followed by a summarizer. Retrieval, evidence retention, model context, factual
sufficiency, identity resolution and commercial reasoning are separate boundaries.

The target method is:

1. define the audit objective and mandatory questions;
2. map the first-party information architecture;
3. acquire primary documents before treating search snippets as evidence;
4. verify that every fetched document/block belongs to the target entity;
5. retain full traceable evidence, but minimize the model working set;
6. extract facts without converting missing data into negative facts;
7. resolve identity and critical identifier conflicts deterministically;
8. assess evidence authority separately from coverage breadth;
9. route only relevant evidence into each extraction/reasoning vertical;
10. rank reasoning context by provenance authority before model confidence;
11. make the final commercial hypothesis traceable to evidence;
12. stop with an explicit reason: sufficient evidence or bounded exhaustion with gaps.

## Method comparison

| Analyst practice | Auditor implementation after this checkpoint |
| --- | --- |
| Start from questions, not URLs | Mandatory research verticals plus progressive gap/recovery waves |
| Prefer the official/primary document | Search results remain discovery hints until primary fetch + entity verification |
| Read the site structure before recursively following everything | Audit-relevant first-party frontier; hubs are retained while large service/product/blog leaf catalogs are bounded |
| Keep original evidence | Document/block locator + document/evidence digest remain in the verified ledger |
| Do not reread boilerplate as independent evidence | Model-only first-party quote dedup; persistent/public evidence is unchanged |
| Distinguish “searched” from “proved” | `search_complete` and `evidence_sufficient` are separate states |
| Strong claims need strong sources | Per-vertical evidence-level thresholds; weak registry/finance/legal evidence does not close the gap |
| More links do not automatically mean higher quality | Evidence quality is authority-weighted on unique traceable documents, not boosted by document count |
| A projection artifact must not change evidence quality | Synthetic S1 and the fetched version of the same canonical URL are one quality document |
| Identity is resolved before downstream inference | Deterministic sourced-name + authoritative INN/OGRN resolver |
| Contradictory legal identifiers are explicit blockers | Multiple authoritative normalized INN/OGRN values produce `identity_state=conflicting` |
| A mirror is not legal authority | Unsourced DaData mirror facts do not resolve identity |
| Provenance beats model self-confidence | Bounded dossier ranking: authority → independent supporting ids → model confidence → period/order |
| Expensive reasoning sees a bounded dossier, not the entire raw ledger | Full extraction ledger remains authoritative; reasoning projection has explicit omitted counters |
| Main conclusion must be citable | `CommercialOpportunity.source_ids` is filtered to materialized evidence |
| Stop honestly | `search_stop_reason` distinguishes sufficiency from bounded exhaustion |

## Current execution pipeline

### 1. First-party acquisition

The root document and audit-bearing pages are discovered before broad external
enrichment. Deep crawling does not recursively exhaust every product/service/blog
leaf. Contact, requisites, company/history, team/doctors, licences, reviews,
vacancies and price-like pages remain eligible.

### 2. External discovery

Search is progressive. The first wave is bounded to the business-critical query
kinds. Missing mandatory directions produce a deterministic gap wave. A final
bounded recovery/enrichment wave may follow. Search snippets remain discovery
hints and cannot become report evidence directly.

### 3. Verification and entity matching

Selected URLs are fetched through the document pipeline. Evidence promotion
requires target-entity confirmation. Relevant blocks retain locator and digest.
Publisher/competitor/mentioned-only blocks cannot become target-sensitive facts.

### 4. Evidence ledger versus model working set

The verified ledger is the provenance boundary and is not shortened for model
convenience. Before extraction, an LLM projection removes only exact normalized
first-party quote duplicates inside the same semantic `query_kind`. Independent
registry/finance/court/etc. documents are not collapsed by this projection.

Research status exposes:

- `llm_source_records_input`;
- `llm_source_records_output`;
- `llm_duplicate_official_quotes_removed`.

### 5. Extraction scheduling

Ordinary mode keeps the 12k-character evidence unit. Deep mode uses a denser
24k-character unit to reduce provider fan-out. If a model response truncates,
the existing recursive subdivision path splits that evidence unit instead of
dropping facts.

Evidence is routed by extraction vertical. Broad first-party profile slices remain
available where appropriate; narrow management/ownership/signal slices require
topical markers. External sources are routed by semantic query kind.

### 6. Consolidation and identity

The extracted ledger is consolidated deterministically:

- placeholder values are removed;
- formatting-equivalent facts are merged with provenance;
- sensitive facts supported only by publisher/competitor/mentioned-only evidence
  are rejected;
- canonical top-level company name prefers consolidated `brand_name`, then
  `legal_name`, rather than the first SEO-heavy chunk title.

Identity readiness is calculated from sourced facts. External authority requires
traceable document and evidence digests. The state is:

- `resolved`: sourced name + one confirmed/corroborated INN or OGRN;
- `provisional`: partial or weak sourced identity;
- `conflicting`: multiple authoritative INN or OGRN values;
- `unresolved`: insufficient sourced identity.

DaData remains a non-authoritative registry mirror and its unsourced facts do not
close identity.

### 7. Coverage and evidence quality

Coverage and quality are independent.

A vertical becomes `covered` only when its evidence meets its threshold.
Identity, financials and legal events require at least `corroborated_signal`.
Contacts, ownership, workforce and operations may use verified `weak_signal`
evidence.

`search_complete` means mandatory search directions have been attempted.
`evidence_sufficient` means every mandatory vertical is actually covered.

Evidence quality is scored on unique real-world documents:

- confirmed fact: 1.00;
- corroborated signal: 0.80;
- weak signal: 0.35;
- unverified mention: 0.00.

An external document receives its declared authority only when both document and
evidence digests are present. Repeated child records from one document contribute
once. A traceable fetched root replaces the synthetic S1 projection for the same
canonical URL.

### 8. Bounded reasoning

The complete consolidated profile remains available in the durable extraction
ledger. Expensive KM/commercial reasoning uses a bounded dossier with per-field
limits and explicit omitted counters.

Selection priority is:

1. deterministic provenance authority;
2. number of supporting source ids;
3. extraction-model confidence;
4. period presence / original deterministic order.

The reasoning prompt receives the compact `source_authority_by_id` map and is
explicitly forbidden to upgrade weak evidence because it is commercially convenient.

### 9. Commercial conclusion

The selected `CommercialOpportunity` now carries `source_ids`. Source ids that
do not exist in the materialized evidence set are removed deterministically.
A source referenced only by the commercial conclusion is still retained in the
result evidence set.

This establishes traceability. It does not by itself prove that the source
semantically supports every word of the hypothesis; that remains a future
claim-level validation layer.

## Measured regression that motivated the changes

Controlled Aleks Dent Stage regression on exact SHA
`5edd656b548bbdbfd4a281291cfe3d7816c47314`:

- documents attempted: 217 → 22 (-89.9%);
- search attempts: 37 → 22;
- completed extraction chunks: 234 → 214 (-8.5%);
- LLM calls: 252 → 231 (-8.3%);
- prompt+completion tokens: about 1.592M → 1.439M;
- LLM elapsed: 550.3s → 662.1s;
- the 12-minute harness still stopped the mission.

Conclusion: the crawl frontier fix worked; the dominant bottleneck moved to evidence
packing/extraction. PR #947 therefore introduced deep-only 24k evidence units and
PR #948 introduced model-only first-party quote dedup. These changes are covered by
CI but have not yet been re-measured in a new paid exact-SHA frozen regression.

## Anti-patterns

The following are explicitly not accepted as success:

- counting search snippets as evidence;
- increasing evidence quality because more weak URLs were collected;
- closing identity/finance/legal gaps with weak evidence;
- treating DaData mirror output as FNS authority;
- sending every verified block to every extraction vertical;
- dropping evidence merely to reduce context;
- letting model confidence outrank stronger provenance;
- silently choosing one of two authoritative INN/OGRN values;
- calling bounded-search exhaustion “sufficient evidence”;
- publishing a commercial score with invented source ids.

## Remaining gaps

The method is materially closer to a careful analyst, but not yet equivalent in
all respects:

1. source independence/diversity is observable only indirectly; authority scoring
   does not yet model common ownership or publisher dependence;
2. document publication/effective dates are not consistently available, so
   freshness is still weaker than a manual recency check;
3. non-identity contradictions (for example conflicting financial values for the
   same period) are retained but not all are classified deterministically;
4. commercial `source_ids` establish provenance, but there is not yet a separate
   deterministic claim-level validator proving that each cited source supports the
   exact problem hypothesis;
5. the post-#947/#948 performance and quality changes still need a new controlled
   exact-SHA paid frozen-company regression before runtime success can be claimed.

## Next acceptance

Do not increase crawl breadth again unless evidence gaps require it.

The next paid frozen regression should compare, at minimum:

- documents/search attempts;
- LLM calls/chunks/tokens and elapsed time;
- LLM projection input/output/dedup counters;
- identity state and conflict counters;
- evidence-quality authority distribution;
- search completion versus evidence sufficiency and stop reason;
- retained legal name, INN, OGRN, director and other frozen identity facts;
- commercial opportunity source ids and their materialized evidence;
- absence of foreign-entity sensitive facts.

Until that run exists, CI proves the deterministic contracts but does not prove
the final real-company runtime outcome.
