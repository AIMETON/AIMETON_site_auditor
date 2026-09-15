# User research execution — supported time policy v0.1

## Search spending observations

Checkpoint: 2026-09-15T14:24:16.262759+00:00. Continuation of #915 after #921.
Each dispatched SearchGateway attempt now checkpoints its configured tariff estimate
in the bound research run, including retries and interrupted/failed calls. Decimal
totals retain currencies separately; no FX conversion or invented tariff is used.
Unknown paid pricing has a separate counter. Denied work and cache responses do not
invoke the dispatch hook. The async site-audit UI labels these amounts as search
tariff estimates, not confirmed billing or the total audit cost. Provider LLM billing
remains unknown; monetary_cost=not_reported and monetary threshold status=unknown
remain honest until reconciliation is implemented. This adds observation only, no
new admission gate or user approval step. Tests cover exact decimal accumulation,
separate currencies, unknown pricing, retry costs, cancellation and denied work.


## Owner correction: account for spending, continue execution

Checkpoint: 2026-09-15T14:20:07.784980+00:00. Part of #915; supersedes the monetary/token admission
gates introduced in #918. The owner explicitly provides a debugging budget and
requires accounting and notifications, not automatic restrictions on paid debugging.

New audit snapshots use spending_policy=account_only. Saved cost/token thresholds
(including legacy fields named limit) are advisory planning values. Their presence,
legacy pause preferences and unknown price do not block audit launch or subsequent
LLM calls. Effective policy explicitly reports both monetary/token enforcement=false,
notify_continue and allow_unpriced; requested revisions remain immutable.

Token threshold status uses observed token totals and appears in run checkpoints;
the async audit UI reports reaching a threshold while continuing. Missing usage
remains unknown. Monetary totals are still not_reported, monetary threshold status
is unknown; this change does not claim complete billing reconciliation. Completing
provider monetary accounting remains work to do, never a prerequisite for debugging.
The separate reservation-ledger foundation is not activated as an audit gate.
User-selected timeouts, explicit stop, ownership/CSRF and provider access controls
retain their existing purpose. No new spending permission prompt is introduced.

Validation: regression coverage includes authenticated audit acquisition with saved
cost/token planning values, a subsequent LLM call after crossing the token threshold,
legacy unknown-price pause compatibility, immutable effective policy and UI launch.

## Historical record (policy above takes precedence)

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
