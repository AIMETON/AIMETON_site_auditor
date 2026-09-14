# Audit Research Profile Loop v0.1

Checkpoint: 2026-09-13T08:12:12Z. Status: implementation candidate; not deployed.
Base: `a9d08d74c2fa955d5299c2b7e698a387bb352d92` (PR #903).
Related: #173, #743, PR #744; `CONVERSATIONAL_RESEARCH_LOOP_V0.1.md`,
`SEF-COMPANY-PROFILE-V0.1.md`, `DADATA-REGISTRY-MIRROR-V0.1.md`.

## Confirmed gaps at the base

- Sync URL audit used a legacy monolith; async URL audit searched but did not
  invoke the existing external document verification/DaData path. Owned missions
  invoked verification but still used the legacy synthesis call.
- Scraper cut normalized text at 45,000 characters. Owned mission aggregation
  applied page and aggregate prefix caps before extraction.
- External verification retained one identity quote (800 characters), not the
  acquired document's full blocks. Split projection/assembly could cut quotes
  again and assembly could expose referenced discovery snippets as sources.
- Chat returned text only; it could neither execute a refinement nor revise the
  company profile. These are current-code findings, separate from historical
  #703/#704/#735 regressions already addressed by #744.

## Implemented candidate

1. Sync, async and owned URL audits use the same verified pipeline and bounded
   split synthesis. Company intelligence also uses bounded synthesis and primary
   document verification.
2. Audit adaptive search resolves the persisted ADMIN policy used by Hunter and
   executes through the existing SearchGateway. Missing policy fails closed for
   search, while a preliminary site-only result remains possible. No provider
   configuration, prices, quotas or paid fallback authorization are changed.
3. Search uses site-derived identity anchors and explicitly searches company,
   product, team and requisites subpages. Discovery classification ambiguity no
   longer prevents fetching; document-level identity confirmation still gates
   evidence. INN/OGRN query anchors pass existing checksum validators.
4. Verification retains independently promoted, locator/digest-bearing chunks
   from every fetched block, including late blocks. At most 24 documents are
   attempted with concurrency four and a 60-second verification deadline. Fetch
   byte/redirect/SSRF/host limits remain. Unfinished documents remain candidates
   or discovery hints. Input acquisition limits do not become silent text cuts.
5. If an official subpage reveals the first valid INN/OGRN, one follow-up wave
   queries FNS EGRUL, GIR BO and requisites/branches. It shares the trace-bound
   mission budget, attempts at most eight documents and has a 25-second
   verification deadline. DaData remains a mirror, never FNS authority evidence.
6. Only verified external evidence enters audit synthesis. Verified records are
   available to all extraction verticals, because a document found by a registry
   query can also contain managers, products and financial data. Acquisition and
   source projection preserve complete supplied text. Existing fast-path overflow
   remains explicit. After successful extraction, a reasoning failure returns
   the extracted facts with a degraded provider state instead of discarding them.
7. `/api/chat` accepts additive `refine_search` (default false). An explicit
   refinement builds three bounded, visible queries from feedback and the current
   company. It refetches the official page and uses the same verified audit path.
   Ordinary discussion makes no search calls. User assertions remain separate
   `user_clarifications`; they are never copied into official page evidence.
8. The response retains `reply` and adds `analysis`, `revision_id`, `added_facts`
   and `search_queries`. `SiteAnalysis` adds `profile_revision`,
   `user_clarifications`, `research_status` and `research_queries`. Merge retains
   previous facts, periods and differing values, and namespaces new source IDs.
   A failed refinement retains the preceding profile. The UI applies the revised
   report and shows its revision, source counts, query plan and user assertions.
9. Dialogue turns append before/after snapshots, messages, planned queries,
   response, UTC timestamp and payload SHA-256 to `audit_dialogue_revisions` in
   the configured Runtime DB. There is no public lookup by caller-supplied ID.
   Existing per-analysis browser history is retained. This legacy adapter is not
   an authenticated cross-device consultation-thread API.

## Trust and completion

All results remain preliminary (`client_release_eligible=false`). Record counts
are not independent-document counts. `extraction_input_coverage_complete` means
input processing coverage only, not semantic fact recall. Per-chunk DTO limits,
LLM recall and the 16-unit fast-path admission bound remain limitations; a long
corpus can return partial/fallback results with retained verified source records.
The concise business/commercial summary is not a substitute for the fact ledger.
Differing values are retained for review; list-valued fields are not automatically
classified as contradictions. Registry documents still require the canonical SEF
identity/claim/release process before client release.

## Verification and remaining acceptance

- Local full suite: 1362 passed, 1 xfailed, six subtests passed; no live provider
  calls. App import and `node --check static/app.js` pass.
- New tests cover late scraper/aggregation facts, full block recovery, rejection
  of another entity, document timeout, verified-only LLM input, dialogue with and
  without search, revision persistence/digest, merge periods/source remapping,
  and preservation of extracted facts on reasoning failure.
- Reproducible before/after acquisition evidence:
  `docs/evidence/audit-profile-loop-regression-2026-09-13.json`. On the same
  synthetic HTML, base kept 45,000 chars and lost TAIL_FACT; candidate kept 50,032
  chars and all three markers. This measures scraper retention, not LLM recall.
- Not performed: four-historical-SHA real-company LLM comparison, exact-SHA stage
  deployment, live search/DaData/FNS acceptance, authenticated cross-device
  dialogue recovery, and paid quality acceptance. No success is claimed for these.

Next acceptance: review/CI → controlled merge and exact-SHA stage convergence →
repeat one frozen company dossier through site/search/verification/profile/chat,
checking late facts, query provenance, source identity, remaining gaps and costs.
Keep #173 open until runtime evidence establishes the intended mission outcome.
