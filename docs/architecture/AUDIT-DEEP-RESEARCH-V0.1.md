# Opt-in deep audit research v0.1

Checkpoint: 2026-09-13T13:30:00Z. Implementation candidate in PR #906; not deployed.
Extends [Audit Research Profile Loop v0.1](AUDIT-RESEARCH-PROFILE-LOOP-V0.1.md).
Owner decision: explicit checkbox may authorize uncapped aggregate LLM spend for
one detailed company audit or dialogue refinement. Default remains standard.

## Contract

- `deep_research=true` and `unlimited_llm_budget=true` must be supplied together.
  Consent requires an existing authenticated session and CSRF validation. The
  owner and consent are checkpointed for this run; no global budget is changed.
- UI offers the checkbox for initial audit and chat scouting, with time/cost
  explanation. Deep chat uses a background research job and updates the profile.
- Deep mode removes aggregate audit/extraction/reasoning deadlines, the 16-unit
  admission ceiling, total document selection caps and profile DTO list/value
  caps. All acquired evidence units reach all relevant extraction verticals.
  Per-call context/output limits remain; truncated output triggers evidence
  subdivision and extraction, never truncation of the returned fact list.
- Official-site links are followed until the discovered frontier is exhausted
  or stopped, with canonical-URL deduplication and robots checks. External
  discovery documents are fetched and identity-verified. Concurrency is four;
  technical request, byte, redirect and provider limits remain. Acquisition
  failures and incomplete evidence coverage remain visible.
- Search uses the existing Hunter SearchGateway and persisted ADMIN policy.
  This consent removes application LLM budget ceilings; it does not override
  search-provider quotas, provider access controls or ADMIN paid-search policy.
  Unlimited mode cannot promise exhaustive coverage of the Internet.
- UI displays documents, LLM calls, reported input/output tokens and completed
  extraction chunks. Monetary cost is `not_reported` because no authoritative
  LLM tariff is supplied. Token usage is never presented as a currency estimate.
- Owner-scoped stop prevents new calls and waits for in-flight requests to finish;
  successful chunks survive and the partial profile carries incomplete coverage.
  A terminal job cannot be changed back to running by a late stop request.
- SQLite checkpoints preserve consent, acquired corpus, usage, successful chunks
  and failures. Running controls are process-local. Restart detection remains
  explicit; automatic continuation and cross-device consultation are not shipped.

## Validation and remaining acceptance

Regression tests cover consent, anonymous rejection, owner-scoped stopping,
85 extraction calls across more than 16 units, 30 separate management facts,
truncation subdivision, retained partial results, uncapped per-run accounting,
absence of aggregate runtime deadline and traversal beyond 24 documents with
robots enforcement. Provider calls are mocked; no paid live research was run.
Local full suite: 1371 passed, 1 xfailed, 6 subtests passed. After the final
subdivision partial-result fix: all 10 deep-mode tests passed. JS syntax and
`git diff --check` passed.

Next acceptance: CI on the new PR head, then deployed exact-SHA long-company
fixture with measured source/fact coverage, real provider token reporting,
stop during acquisition/extraction and dialogue profile revision. No historical
four-SHA live company comparison or production quality recovery is claimed.
