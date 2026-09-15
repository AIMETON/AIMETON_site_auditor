# User research execution — supported time policy v0.1

Checkpoint: 2026-09-15T09:20:34.513355+00:00. Part of #915. Implementation candidate after merged #917.

## Applied behaviour

The owner-scoped GET/PUT settings API is mounted. The settings panel saves separate
site-audit and company-intelligence revisions. The backend applies existing saved
preferences to authenticated audit launches even when the client omits the revision;
a supplied stale revision fails with HTTP 409. CSRF is required before execution.
Before provider work, the requested/effective policy and digest are persisted as an
immutable snapshot. Concurrent preference changes cannot alter a running audit.
Saving settings is distinct from the user's subsequent explicit audit submission.

Supported runtime controls:

- mission_timeout_seconds: one wall-clock deadline spanning acquisition and synthesis;
  null removes the user aggregate deadline. It does not remove individual call bounds.
- request_timeout_seconds: bounds each static/browser/document fetch and each search
  provider attempt, including redirects/body reads; underlying client bounds also use
  the selected value. Existing URL, host, SSRF, byte and provider permission checks remain.
- llm_call_timeout_seconds: bounds all six existing LLM call functions, including legacy
  synthesis, strict synthesis, chat helper, observer and query planning when invoked
  inside a settings-bound audit. Standalone chat/refinement launches are not enrolled.
- progress_warning_seconds: records a warning without cancelling the audit; the async
  site-audit UI shows it. Company audit is still synchronous, so live company warnings
  are not delivered through a polling channel yet.
- retry_count and retry_backoff_seconds: applied by the existing SearchGateway to
  retryable search failures. Backoff is the configured fixed interval. These controls
  do not add automatic retries to LLM calls or alter provider permission/budget gates.
- hard_limit_action=stop: aggregate expiry records mission_timeout, cancels awaited
  work, and cannot be converted by broad analytical fallback handlers into success.

For settings-bound search, SearchGateway uses its existing implementation directly,
without cross-mission in-flight coalescing or shielding; cancellation must reach all
provider tasks owned by that run. Cache/routing/permission decisions remain in the
existing gateway. Trace timeout/retry metadata reflects selected settings. Legacy
aggregate analysis/synthesis/verification stage deadlines are bypassed only inside
settings-bound runs; the user mission deadline and per-call bounds replace them.

## Unsupported guarantees fail before execution

Monetary/token warning or hard-limit fields can be saved but block audit launch with
HTTP 409 until shared authoritative tariff/token bounds cover all paid calls. Unknown
price defaults to no execution; proceeding without a monetary cap requires explicit
allow_unpriced selection. No zero-price assumption or fabricated tariff is introduced.
A finite aggregate deadline with action=pause is rejected because durable resume is
not implemented. execution_enabled and execution_block_reason report whether a saved
configuration is currently executable; execution_scope describes the supported subset.

The #917 reservation ledger remains a tested foundation, not activated provider
accounting. This slice does not claim completion of all P0-B/C acceptance criteria.

## Lifecycle boundaries

Async site audit uses canonical mission/analysis IDs and ends with failed plus an
explicit stop reason on aggregate expiry. Synchronous site/company endpoints return
HTTP 408. A timeout is not a completed analytical report. Existing persisted evidence
and extraction checkpoints are retained; reconstruction/return of a complete partial
report on interruption remains P0-D work.

Company settings snapshots use an explicit transitional company-run:<run_id> scope,
owned by the authenticated user. This is not represented as a canonical orchestrator
mission: the current orchestrator creation contract requires a known URL, whereas
company search may start with a name only. Unified owned async company missions,
resume/recovery, retention/deletion and MCP parity remain open P0-D work. Audit
settings are not applied to standalone chat/refinement; explicit settings revisions
on those endpoints are rejected rather than ignored. Legacy users with no saved
revision preserve their prior launch behaviour.

UI requests carry CSRF and revision. Unsaved changes block launch, save/load controls
are disabled during a pending request, and account changes invalidate pending responses
and clear displayed preferences. Monetary strings preserve decimal precision.

## Validation and remaining live gates

Tests exercise real authenticated audit endpoints with fake acquisition/providers:
pre-acquisition monetary blocking, omission/stale-revision protection, acquisition-wide
timeout, cancellation of formerly shielded search, LLM wall-clock bounds, configured
retries/backoff, non-interrupting warnings, immutable snapshots across preference edits.
A Node DOM/network event harness executes the actual settings script for save/launch,
unknown-budget blocking, decimal payloads and stale response rejection after logout.
CSS was adjusted for desktop/mobile grids and JavaScript syntax checked.

Visual browser acceptance is not claimed: local Chromium is absent and downloads from
the Playwright CDN timed out. DOM-event and API tests are independent evidence, not a
substitute for a screenshot or deployed authenticated end-to-end acceptance. No paid
live audit was triggered. Post-merge CI/deploy and exact-SHA health are separate gates.

Final local validation: 1471 passed, 1 xfailed, 6 subtests passed; JavaScript syntax and git diff whitespace checks passed.
