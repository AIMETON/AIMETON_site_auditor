# User research execution — supported time policy v0.1

## Large evidence corpus: schedule chunks instead of rejecting coverage

Checkpoint: 2026-09-17T01:01:01.022541+00:00. Part of #915; follows #929 live validation.
On deployed 8b6676f6baa9268c9a2a348f63c8bb823f6ed675, the Selectel run
mission_4228d36e7e604167b320ece9c0035a06 / analysis_aead1caf8da948a2b11ac37f0b82b106
exposed partial_result while running, then completed with a degraded final report:
4068 source/evidence entries, 8 company facts, 27 finished queries, 81 finished
provider calls (8 failed). RouterAI failed after 0.4 seconds; the report records
EvidenceCoverageOverflow. Estimated input was 4,968,872 characters. This is proof
of partial-report delivery, not successful full company synthesis. The public trace
does not distinguish the exact overflow branch; code had both a 16-unit extraction
gate and a large-input monolith gate, and regression tests cover both.

The new scheduling behaviour plans all evidence units. Above the fast-path size,
ordinary extraction uses the existing checkpointed chunk processor, loss-preserving
output schemas, output-truncation subdivision and shared concurrency of four. A
large monolith input routes to split synthesis even when monolith is selected for
small inputs. No prefix trimming, evidence dropping or paid-operation permission
gate is added. Automatic scheduling does not set deep_research consent or widen
the search frontier. Existing controls are reused; calls without one get a locally
scoped accounting control for extraction checkpoints, not a new owned mission.

User-selected deadlines and explicit stop remain effective. Legacy aggregate
deadlines for runs without saved settings still apply: this change removes the
size rejection, not all remaining lifecycle/time constraints. A deadline or provider
failure can still leave incomplete extraction. Full live synthesis of the Selectel
corpus on the new scheduler remains a separate acceptance task. Monetary totals
remain unconfirmed; provider call counts are not invoices.

The large status response was 4,070,180 uncompressed bytes; existing zstd negotiation
transferred 424,919 bytes. No truncation or duplicate compression layer was needed.
Validation: 1488 passed, 1 xfailed, 6 subtests locally. Tests cover late official-text
and external-source facts, complete unit accounting, concurrency and routing away
from an undersized monolith.


## Partial site report survives interruption

Checkpoint: 2026-09-16T16:28:30.112378+00:00. Part of #915, P0-D.
Async site audit now saves a local heuristic report immediately after site acquisition,
before awaiting external enrichment. The existing durable analysis projection gains
an additive nullable partial_result_json column; old databases migrate in place.
REST/MCP shared status exposes partial_result separately from result. Mission/analysis
IDs and final fetched URL are retained; readiness stays preliminary and explicitly
blocks client release with audit_not_completed. The report identifies its stage as
site_acquired and quality as partial, with a visible explanation of incomplete research.

Timeout, requested stop, subsequent error or process restart retain that checkpoint.
A stop observed before final commit cannot publish the completed result. Success still
returns result normally. Failed/interrupted runs do not become completed just because
a partial report exists. UI shows partial reports under a separate analysis-partial
event and heading, retains export/history metadata and stops polling a confirmed
restart interruption. Transient degraded/blocked phases no longer end UI polling.
The initial polling timer is registered before the first awaited poll, avoiding a
new timer after a terminal response has already cleared it.

Scope is the local site-acquisition report, not a merger of all in-flight external
evidence/chunk checkpoints or a reconstruction of raw page text. Failures before
acquisition still have no partial report. Sync company/site responses and durable
provider resume remain outside this slice. No extra provider calls, spending caps
or permission gates are introduced. Tests cover timeout, stop, error, success,
restart, old-schema migration and real workspace partial/completed event rendering.
Full local suite: 1486 passed, 1 xfailed, 6 subtests. Live deployment is checked separately.


## Recovery of research accounting after process restart

Checkpoint: 2026-09-16T06:27:41.834941+00:00. Part of #915, P0-D accounting continuity.
The async analysis identifier and research run identifier were distinct. #923 persisted
spending checkpoints but REST/MCP status read usage only from in-memory controls.
After restart the durable analysis remained visible while its counters disappeared.

New async site/refinement launches persist an immutable analysis-to-run/owner binding
before scheduling provider work. Each research checkpoint atomically also writes a
public status snapshot. LLM dispatch now checkpoints the started call before awaiting
a response, so a crash cannot erase that observed call merely because usage is absent.
REST/MCP's shared status projection reads the latest saved snapshot when live controls
are unavailable; recovery is marked with accounting_recovered and checkpoint time.
UI labels recovered counters and hides the non-functional stop control for such runs.
Readback never resumes providers, recreates consent, changes audit state or implies a
confirmed bill. No spending admission gate is added. Only existing public counter
fields are exposed, not owner IDs, settings payloads or provider contents.

Old unbound runs cannot be reliably associated retrospectively and remain without
recovered counters; no guessed association or invented zero is returned. Missing or
corrupt accounting does not hide the durable analysis status. This is a recovery of
last observed accounting, not billing reconciliation or durable worker resume.
Company async execution, partial reports and complete monetary reconciliation remain
open plan items. Tests cover actual launch binding with/without saved settings, loss
of process dictionaries, interrupted LLM calls, currency estimates, immutable binding,
run isolation and unavailable/corrupt accounting.


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
