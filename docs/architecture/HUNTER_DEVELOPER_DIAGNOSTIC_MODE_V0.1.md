# Hunter Developer Diagnostic Mode v0.1

Status: proposed / implementation-ready

Related: #909, #467, #476, #544, #297

## Problem

Hunter already records a sanitized forensic trace, but the operator UI exposes only the final result list and aggregate funnel. When precision, provider quality, query expansion or candidate processing regresses, a developer cannot answer from the product UI:

- which query produced a URL;
- which provider/attempt returned it;
- why it survived dedupe and qualification;
- which pre-score factors fired;
- whether deep processing failed;
- which fallback path retained or rejected the item;
- where the funnel count changed.

This makes live acceptance unnecessarily dependent on server-side log access and makes defects such as #909 difficult to localize.

## Design principle

Do not create a second logging subsystem. `HunterForensicTrace` / `TraceLedger` remains the canonical diagnostic event stream. Developer mode is an authorized projection over that evidence plus bounded live progress data.

Normal customer responses remain unchanged and do not expose internal trace identifiers or provider diagnostics.

## Activation

Add a `Режим диагностики` switch to the Hunter search form. The control is rendered only when the authenticated principal has an existing privileged developer/admin capability or when the deployment is an explicitly authorized local/stage diagnostic contour.

Recommended request contract:

- normal run: existing API contract;
- diagnostic run: explicit `debug=true` (or equivalent typed option) attached to the hunt mission;
- the backend returns a sanitized diagnostic handle only to an authorized caller;
- unauthorized use fails closed and does not silently downgrade into information disclosure.

The debug switch is per mission. It must not become a global mutable runtime mode.

## UI

When enabled, render a separate diagnostics surface next to the ordinary result view. Recommended tabs:

### Query plan

Show:

- original and effective region;
- original and normalized industries/focus;
- plan source: LLM or deterministic fallback;
- generated query list in execution order;
- deterministic validation state for every proposed query;
- rejection reason for invalid query variants;
- Search Observer / steering decisions where enabled.

### Providers

For each query:

- provider/engine;
- attempt state;
- result count;
- latency;
- cache/singleflight state;
- typed error/degradation reason;
- bounded cost/usage fields already available from provider telemetry.

Never render credentials, Authorization headers, API keys, raw provider tokens or secret configuration.

### Raw results

For every raw result retained in diagnostic evidence:

- query/direction;
- provider;
- provider/raw rank;
- title;
- URL/domain;
- bounded snippet;
- dedupe identity;
- final dedupe action.

### Qualification

Show candidate decision data:

- pre-score status and numeric score;
- factor map (`region_match`, `industry_match`, commercial markers, focus, local domain);
- source role;
- lead fit;
- configured thresholds;
- reject / observation / deep-audit decision;
- region confirmation state;
- final ranking role.

### Deep audit

Show:

- deep-audit start/completion/failure state;
- fetch path/state where available;
- sanitized exception type;
- heuristic/analysis score;
- final score;
- downgrade reasons.

### Funnel / trace

Chronological sanitized events with counters for:

`raw → excluded → deduplicated → unique → inspected → qualified → returned / omitted`.

Include mission timestamps and correlation identifiers only inside the authorized diagnostic surface.

## Backend projection

Add a privileged diagnostic endpoint or authorized expansion endpoint that reads the existing trace ledger by mission/attempt and returns a bounded schema. The endpoint must project only whitelisted fields and reuse trace-ledger redaction.

Minimum response groups:

- mission metadata;
- query plan;
- provider attempts;
- raw/dedupe events;
- qualification events;
- deep-audit events;
- funnel summary.

The API must support polling while a long-running Hunter mission is active so diagnostics are useful before completion. Existing Hunter progress events may feed the live counters, while durable trace events remain the source of truth for post-run explanation.

## Security and data handling

- privileged authorization is mandatory;
- no secrets or raw auth headers;
- no raw LLM system prompts unless a separate explicit privileged policy is introduced later;
- snippets and metadata remain bounded;
- diagnostic payload follows trace retention policy;
- normal public/customer API serialization must not start exposing `trace_mission_id` / `trace_attempt_id` merely because diagnostics exist.

## Precision invariants surfaced by diagnostics

The diagnostic view should make these invariants machine- and human-checkable:

1. Every executed LLM-generated query retains deterministic region and industry anchors.
2. A calculated candidate below the request's `minimum_pre_score` never appears in `candidates[]`, including exception/fallback branches.
3. `insufficient_data` is distinguishable from a qualified commercial company.
4. Region displayed as confirmed must come from evidence, not simply from the search request label.
5. Supporting sources and observations do not compete with direct companies in the same ranking tier.

## Acceptance

Canonical live scenario: `Красноярск / Стоматология`.

A developer must be able to pick any returned or rejected URL and answer, from the site UI alone:

1. what query produced it;
2. what provider returned it;
3. what score factors fired;
4. which threshold/role decision occurred;
5. whether deep processing succeeded or failed;
6. why the record is visible in its current result group.

When developer mode is off, the normal search UI must remain unchanged and uncluttered.
