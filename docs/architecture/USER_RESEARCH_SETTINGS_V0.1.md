# User research settings — P0-B foundation

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

Checkpoint: 2026-09-15T03:33:54.995917+00:00
Status: draft implementation, not connected to production execution. Part of #915.

## Implemented

ResearchSettings defines user mission/request/LLM timeouts, warning thresholds,
Decimal monetary limits and currency, token limits, retries, and warning/hard-limit
behaviour. Null means no user aggregate limit; omitted fields use versioned defaults.
A hard monetary limit cannot coexist with allow_unpriced. Negative/zero/non-finite
limits and warnings exceeding caps are rejected. retry_count=0 explicitly disables retries.

ResearchSettingsRepository uses the existing runtime SQLite location. Rows are
owner/service/revision scoped. BEGIN IMMEDIATE and expected_revision prevent
lost updates; a conflict maps to HTTP 409. Revisions are retained. Current presets
are independent site-audit/company-intelligence settings; common-default inheritance
and its migration remain pending.

Internal snapshots bind owner/mission/run/settings revision/time/digest. Overrides
are validated; explicit null is preserved. Retries return the immutable snapshot,
not newer preferences. Different overrides for an existing run are rejected.
Only a caller that has already resolved mission ownership may create a snapshot.
No public snapshot endpoint is exposed.

The draft router supports GET/PUT /api/user/research-settings/{service}, derives
owner from the existing session, validates CSRF on writes and rejects caller owner_id.
It is intentionally NOT mounted in app.main. Tests mount it in an isolated app.
Responses say execution_enabled=false; snapshots have effective=null and
pending_integration until P0-C compiles and applies provider policy.

## Remaining delivery gates

- settings UI with persisted per-service defaults and explicit per-run overrides;
- owned mission launch binding and requested/effective policy compilation;
- monetary reservations/settlement, tariff provenance and concurrent accounting;
- request/LLM/aggregate time and threshold enforcement with retained partial evidence;
- mount router and expose user controls only with end-to-end behavioural tests;
- account/mission deletion and retention policy integration for new tables;
- cross-device and deployed acceptance.

No expenditure authorization is inferred from saving preferences. Runtime spending,
existing deep consent and provider policy are unchanged by this draft.

## Evidence

16 focused tests passed: invalid values, exact money, owner/service isolation,
restart persistence, concurrent saves, immutable snapshots, auth/CSRF and HTTP 409.
No paid live calls. This is foundation evidence, not completed P0-B/C acceptance.

## P0-C admission accounting checkpoint — 2026-09-15T07:56:45.410907+00:00

`research_budget.py` adds a durable internal reservation ledger in the existing runtime
SQLite database. This extends the accounting concern; it is not a second provider
controller. Existing `ai_cost_accounting.py` remains the post-hoc attempted/billed/
accepted-cost projection, not an admission authority.

A run binds an immutable settings payload and snapshot digest. Atomic reservations
count settled actual usage plus outstanding upper bounds. Money stays Decimal/string;
SQL floating point is not used. A unique owner/run/request key prevents double
reservation, and an atomic claim permits only one dispatch. Each billable retry needs
a new request key and reservation. A claimed request retains its bound after a timeout
or crash: absence of a receipt is not proof of zero billing. Only unsent work can
release a reservation. Complete receipts settle idempotently. A provider exceeding
its promised upper bound records the actual bill and stops future admission; this
incident is not hidden by clamping the bill to the limit.

Unknown prices pause admission by default. Explicit allow_unpriced is only compatible
with no monetary cap; unknown totals are null, never zero. Token caps require a known
token upper bound. Warning thresholds support notify/continue or pause; hard thresholds
pause/stop before admission. Pause/stop state survives restart. This ledger does not
provide a resume API yet. Its totals are committed exposure, not an invoice view.

The ledger is NOT attached to live provider calls yet. Its quote parameters are an
internal contract, not a user-accessible way to assert a tariff. Before activation,
the existing provider adapters must supply authoritative, currency-matched bounds
covering all chargeable input/output/reasoning/cache/tool units and search fees,
with immutable tariff provenance. A string tariff reference alone is not verification.
No hard-cap guarantee is made for production by this draft.

Inspection found missing research counters in legacy monolithic synthesis, legacy
split synthesis, the search observer and Hunter query planning. These adapters now
call the existing per-run accounting hooks before I/O and record provider usage before
output parsing. Existing strict synthesis/chat hooks remain. Missing, malformed or
partial usage is exposed as `llm_usage_unknown`; counters of observed tokens remain
separate. This is telemetry coverage, not money settlement or complete service-wide
accounting: a bound ResearchControl is still required, and company launch wiring is
pending.

Validation: 1453 tests passed, 1 expected failure, 6 subtests passed. New tests cover
parallel admission, exact decimal limits, restart, single dispatch, idempotent billing,
unknown receipts/prices, owner isolation, warnings/stops, bound violations and usage
before schema rejection. No paid live calls. #916 stage deploy run 34925242984 completed
successfully; stage health previously returned merge e4941e5c052842bd9600f941a936160fd44e71bb.
These are separate code/test/deployment observations; live fact-recall acceptance is open.

## Execution integration update — 2026-09-15T09:20:34.513355+00:00

#917 was merged as b74ce39f50bafc852a41809032b7ca852ce3043c and deployed to stage. The next implementation mounts the preference API and adds UI/owned snapshots/applied timeouts. This supersedes the earlier unmounted draft status for that candidate; monetary/token gates remain blocked. See [USER_RESEARCH_EXECUTION_V0.1.md](USER_RESEARCH_EXECUTION_V0.1.md) for exact supported behaviour and remaining acceptance.
