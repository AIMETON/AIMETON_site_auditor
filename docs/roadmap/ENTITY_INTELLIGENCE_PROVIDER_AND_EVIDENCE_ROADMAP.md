# Entity Intelligence Provider & Evidence Roadmap

**Status:** ACTIVE  
**Origin:** IMLS/services-realty.ru field benchmark, 2026-08-21  
**Benchmark:** `COMPETITOR-RU-01`

## Objective

Close the gap between AIMETON's current strong synthesis/commercial reasoning and weak primary-source verification.

Current benchmark baseline:

```text
HTTP success = yes
identity_state = unresolved
sufficiency_level = L0
profile_completeness = 0.60
evidence_quality = 0.0
commercial_priority = 72
legal_events = not_searched
client_release_eligible = false
```

Target: a source-traceable entity-intelligence profile where critical identity, financial and legal claims are verified from primary documents and historical observations are retained.

## P0 sequence

### EI-P0-01 — FNS identity provider

Trigger automatically from discovered INN/OGRN.

Deliver:

- normalized legal identity;
- primary source document metadata;
- executives/founders where available;
- evidence locator/digest;
- accepted identity claims;
- conflict handling.

Benchmark gate: `identity_state=resolved` when evidence is sufficient.

### EI-P0-02 — GIR BO financial provider

Deliver verified multi-period financial observations and derived trend signals.

Benchmark gate: financial vertical moves from discovery/weak to verified/partially_verified.

### EI-P0-03 — FSSP provider

Deliver enforcement proceedings as temporal events, including amount/state where available.

### EI-P0-04 — KAD/arbitration provider

Deliver cases, roles, amounts, counterparties and primary court-document candidates.

### EI-P0-05 — Fedresurs/bankruptcy provider

Deliver bankruptcy/distress lifecycle events with source provenance.

### EI-P0-06 — Evidence promotion pipeline

Enforce:

```text
discovery_hint
-> source_candidate
-> fetched_document
-> exact evidence
-> normalized claim
-> accepted/rejected/conflicting
```

### EI-P0-07 — Temporal Evidence Memory

Persist observations append-only with timestamps and supersession/correction semantics.

Minimum projections:

- snapshots;
- diffs;
- events;
- signals.

### EI-P0-08 — COMPETITOR-RU-01 regression scoring

Automate repeatable measurement of:

- identity state;
- sufficiency;
- evidence quality;
- completeness;
- verified verticals;
- primary document count;
- accepted claim count;
- unsupported claim count;
- temporal events/signals;
- release eligibility.

No target-specific hard-coded facts are allowed.

## P1 sequence

### EI-P1-01 — Generic Entity/Relationship Graph

Create typed entity and relation contracts with provenance and temporal validity.

### EI-P1-02 — Cross-source signal engine

Derive economic signals only from evidence-bearing observations, preserving source paths.

### EI-P1-03 — Autonomous investigation planner

Generate bounded pivots from signals/evidence gaps and maintain competing hypotheses.

### EI-P1-04 — One-click entity-check UX

Initial jobs:

- Company;
- Person;
- Sole proprietor;
- Site/domain.

### EI-P1-05 — Continuous Watch

Turn reports into explicit monitoring subscriptions with observable criteria and transparent user controls.

## P2

- Property Intelligence provider pack;
- procurement / contracts / RNP;
- licences;
- patents/trademarks;
- accreditation/certification;
- reviews/maps;
- jobs/workforce;
- marketplaces/prices/listings;
- digital footprint providers;
- additional vertical product packs.

## Borrow / surpass rule

Borrow from mature aggregators:

- source coverage;
- simple user jobs;
- monitoring;
- historical accumulation;
- vertical workflow packaging.

Surpass through:

- primary evidence;
- provenance;
- conflict-aware claims;
- temporal reasoning;
- relationship graph;
- second-order economic signals;
- autonomous hypothesis testing;
- release gates.

## Done condition

The roadmap is not complete when the UI displays more fields. It is complete only when the same real benchmark target produces materially stronger **verified evidence**, not merely richer generated prose.


## 2026-09-13T08:12:12Z — audit integration candidate

Local delivery under #173 connects URL audit to the existing Hunter-governed
search, primary-document verification and DaData mirror, preserving late input
and supporting explicit dialogue refinements. See
[Audit Research Profile Loop v0.1](../architecture/AUDIT-RESEARCH-PROFILE-LOOP-V0.1.md).
This is a candidate contribution to EI-P0-06/07/08, not completion of the FNS,
GIR BO, graph or cross-device consultation roadmap. Next: CI and exact-SHA stage
acceptance with a frozen company dossier and measured query/evidence/profile delta.
Existing dates and prior milestone statuses are unchanged.

## 2026-09-13T13:30:00Z — optional uncapped LLM research

Owner-requested extension in PR #906 adds per-run explicit consent, deeper
acquisition and extraction, usage reporting and cooperative stop. See
[Deep Research contract](../architecture/AUDIT-DEEP-RESEARCH-V0.1.md).
Next: exact-head CI and stage acceptance including partial-result stop and
profile refinement. Provider quotas and cross-device/restart work remain separate;
existing dates and milestone status are unchanged.
