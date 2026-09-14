# Hunter precision/noise incident — 2026-09-14

Status: corrective work in progress

Related: #909, #910

## Trigger

A live `Красноярск / Стоматология` run returned obvious unrelated pages (retail catalogs, dictionaries, media and Windows networking) in the retained result set. Several of those rows carried calculated scores `25/100` or `30/100`, below the configured default `minimum_pre_score=35`.

## Confirmed root cause

`app/discovery.py::inspect()` correctly rejects a calculated candidate when `score < minimum_pre_score`. However the outer `guarded()` error-containment branch recalculated `_pre_score(...)` after an unexpected candidate-processing exception and unconditionally created a shallow candidate. This bypassed the minimum-score invariant and allowed low-score noise to re-enter `candidates[]` and the `qualified_candidates` count.

The fallback branch also used `title or domain` as input to `_pre_score`, whereas the primary branch scores the raw title. That meant the normal and exception paths could make different relevance decisions from the same search result.

## Corrective invariant

For every candidate-processing path:

- the same raw title/snippet/url inputs are used for pre-score;
- a calculated score below the request's `minimum_pre_score` is rejected even after an unexpected processing error;
- the rejection is recorded in Hunter forensic trace with a typed reason code;
- only `insufficient_data` or candidates meeting the configured minimum may remain eligible for shallow fallback.

A regression test injects a processing failure after pre-score for an unrelated catalog result and requires `candidates=[]`, `qualified_candidates=0`, and `returned_candidates=0`.

## Other confirmed contributing defects

1. Before PR #910, LLM Query Intelligence variants were only schema-validated/deduplicated. PR #910 added a deterministic region+industry semantic guard before provider execution.
2. Region is currently a scoring factor rather than a universal hard gate. A dentistry source for another city can therefore remain a supporting/observation source. This requires typed geographic evidence rather than a blind score change.
3. The current UI can display the hunt region as a fallback label when the candidate itself has no confirmed region. The UI must distinguish `requested/search region` from `confirmed candidate region`.
4. `qualified_candidates` currently represents all retained candidate/source/observation rows, not only direct commercially qualified companies; funnel semantics need a later contract refinement.

## Diagnostic mode

`docs/architecture/HUNTER_DEVELOPER_DIAGNOSTIC_MODE_V0.1.md` defines the required developer/admin diagnostic surface. It reuses `HunterForensicTrace` / `TraceLedger` as source of truth and must expose sanitized query, provider, raw-result, qualification, deep-audit and funnel tabs without creating a second logging subsystem.

## Acceptance

Canonical acceptance remains `Красноярск / Стоматология`:

- calculated rows below `minimum_pre_score` never appear in returned candidates, including exception paths;
- generic Query Intelligence drift is rejected before provider execution;
- requested geography is not rendered as confirmed company geography without evidence;
- a privileged developer can trace any retained or rejected URL to query/provider/score/decision evidence once diagnostic UI implementation is complete.
