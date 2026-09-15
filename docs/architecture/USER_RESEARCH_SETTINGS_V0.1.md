# User research settings — P0-B foundation

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
