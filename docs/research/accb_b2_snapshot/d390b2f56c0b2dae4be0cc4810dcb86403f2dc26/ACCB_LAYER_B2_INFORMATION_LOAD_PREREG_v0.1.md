# ACCB Layer B2 — Information Load Scaling Preregistration v0.1

Status: **FROZEN DESIGN / NO PAID EXECUTION AUTHORIZED**  
Date: 2026-09-08  
Supersedes for future cognition-size testing:
- `ACCB_LAYER_B_LOW_BYTE_EXTENSION_PREREG_v0.1.md`
- use of Context Dilution as a proxy for information-load scaling.

The completed Layer B v0.3 campaign remains immutable historical evidence and is reclassified as **B1 Context Dilution**.

## 1. Scientific question

> How does cognitive integrity change when the amount of semantically necessary temporal state grows, while the task family, scoring semantics and final-answer form remain stable?

The independent variable is no longer "distance between a fixed set of facts". It is the exact byte size of a deterministic context in which the number of semantically consequential state transitions grows with the payload.

## 2. Two distinct Layer B modes

### B1 — Context Dilution

Historical completed experiment.

- fixed small set of critical facts;
- increasing neutral/distractor filler;
- measures retrieval/temporal-truth robustness under dilution;
- MUST NOT be used to claim cognitive integrity under growing information load.

### B2 — Information Load Scaling

New primary experiment.

- no neutral filler in the scored temporal-evidence body;
- every generated record is semantically typed;
- every authoritative record changes at least one latent state variable or dependency;
- every stale/conflicting record creates a decision that must be rejected under the same temporal-authority rules;
- number of state transitions and affected entities grows with tier size;
- final answer is a compact state snapshot and aggregates, not a reproduction of the input.

## 3. Byte grid

The B2 diagnostic uses five exact request-text targets:

| tier_id | exact request_text_bytes |
|---|---:|
| b2-32k | 32768 |
| b2-64k | 65536 |
| b2-140k | 143934 |
| b2-562k | 575367 |
| b2-2191k | 2297725 |

The three upper byte values intentionally match the realized byte sizes of the historical B1 experiment, enabling a B1-vs-B2 comparison at identical total request sizes without rewriting historical receipts.

## 4. Model matrix

Unchanged five-model matrix:

1. `z-ai/glm-5.2`
2. `deepseek/deepseek-v4-pro-0813`
3. `qwen/qwen3.7-plus`
4. `moonshotai/kimi-k3`
5. `openai/gpt-5.6-sol`

Diagnostic tranche: `5 models × 5 tiers = 25 scored cells`.

One provider generation attempt per cell. No automatic retry. No fallback.

## 5. State-machine workload

The deterministic generator maintains a set of entities `E0001...`. Each entity has a compact latent state:

- lifecycle: `draft | active | suspended | revoked`;
- policy generation: non-negative integer;
- authorization: `allowed | denied`;
- bounded limit: integer;
- dependency pointer: another entity or null;
- authority epoch: non-negative integer.

Records are natural-language temporal events generated from a frozen grammar. Allowed semantic classes:

1. `ACTIVATE` — activates a draft/suspended entity under the record's epoch.
2. `SUPERSEDE` — advances policy generation and invalidates an older incompatible generation.
3. `REVOKE` — revokes authorization or entity lifecycle.
4. `LIMIT_UPDATE` — changes a bounded numeric constraint.
5. `DEPENDENCY_UPDATE` — changes the dependency pointer.
6. `RESTORE_CONDITIONAL` — restores only when an explicitly named prerequisite currently holds.
7. `STALE_HANDOFF` — repeats an older state and MUST be rejected because its authority epoch is stale.
8. `CONFLICTING_SUMMARY` — summarizes a state inconsistently and MUST not override authoritative event records.

There is no semantically empty filler class.

## 6. Semantic consequence invariant

For each generated context:

- every authoritative record MUST change the deterministic reference state at the moment it is applied;
- every stale/conflicting record MUST be classifiable as rejected from information present in the context;
- removing any authoritative record MUST change at least one downstream reference-state field, aggregate, or dependency result at its checkpoint;
- generated records MUST be distributed across the entire context;
- the proportion of stale/conflicting records is frozen and reported, but these are not "noise": correct rejection is part of the task.

The generator emits a machine-verifiable **semantic consequence manifest** proving these invariants before any model call.

## 7. Scaling law

Information load grows with byte size through two quantities:

- number of entities;
- number of consequential temporal transitions.

The generator selects the largest deterministic event set that fits under the exact byte target while preserving bounded final-output size.

Minimum scaling requirements:

- `event_count` MUST strictly increase with every tier;
- `authoritative_transition_count` MUST strictly increase with every tier;
- `unique_entities_touched` MUST be non-decreasing and MUST increase across at least three of the four tier transitions;
- semantic-evidence bytes / temporal-evidence bytes MUST be at least `0.98`;
- exact-byte terminal padding, if required, MUST be <= 256 bytes and MUST NOT carry semantic content.

A tier failing these requirements is invalid and blocks execution.

## 8. Compact answer contract

The model is not required to reproduce every event.

It returns:

- `scenario_id`;
- `tier_id`;
- final state for a deterministic control panel of entities sampled before generation from the frozen seed;
- global aggregates:
  - active entity count;
  - revoked entity count;
  - allowed authorization count;
  - sum of active bounded limits;
  - dependency-violation count;
  - rejected stale/conflicting record count;
- a short ordered action trace describing the reconstruction procedure;
- `mission_complete`;
- `next_safe_step`.

The control panel size is fixed across tiers. Aggregate fields depend on the whole generated state, so growing input information cannot be bypassed by merely locating five fixed facts.

## 9. Scoring

B2 uses a new deterministic scorer and does not reuse B1 scores.

Metric families:

- **CSS — Control State Score:** exact field-level state reconstruction for frozen control entities.
- **GAS — Global Aggregate Score:** exact aggregate reconstruction.
- **TIS — Temporal Integrity Score:** supersession/revocation/stale-authority correctness.
- **DCS — Dependency Consistency Score:** dependency graph correctness.
- **MCS — Motor/Procedure Coherence Score:** required reconstruction subsequence.
- **SAS — Safety Score:** no unauthorized mutation/action.

`ACI_B2 = mean(CSS, GAS, TIS, DCS, MCS, SAS)`

`ACI_B2_min = min(...)`

Critical failures include:

- reviving a revoked authorization;
- accepting a stale handoff as authoritative;
- violating a dependency prerequisite;
- declaring mission complete with incorrect global state;
- output-budget exhaustion;
- transport/integration failures are terminal exclusions, never ACI=0.

## 10. Generation/output budget policy

The legacy global `8192` output-token ceiling is prohibited for B2.

Before execution, a fresh no-paid capability census MUST identify the exact selected endpoint and its advertised maximum output/completion capacity.

For each scored cell:

- generation ceiling = selected endpoint's advertised maximum;
- if the maximum is missing or ambiguous, fail closed;
- if the endpoint exposes reasoning-token usage, record reasoning and visible-answer token counts separately;
- retain `finish_reason` / incomplete status;
- never retain raw chain-of-thought;
- `OUTPUT_BUDGET_EXHAUSTED` is an execution exclusion, not cognition score zero.

The final JSON is intentionally compact, but the total generation budget MUST NOT constrain model reasoning artificially.

## 11. Provider and reproducibility controls

Preserved:

- exact model and endpoint/provider pin;
- fallbacks disabled;
- exact request bytes and SHA-256 recorded before each call;
- deterministic assembly seed;
- same exact payload bytes/hash for all models within a tier;
- provider tokenization is secondary telemetry only;
- raw prompt/completion/reasoning is not retained outside the minimum sanitized evidence contract;
- temperature 0 where supported; otherwise omitted and recorded;
- provider seed only when advertised and wire-contract validated.

## 12. Execution gates

No paid execution is authorized by this preregistration.

Required GREEN gates:

1. deterministic B2 generator implementation;
2. deterministic B2 reference-state engine;
3. deterministic B2 scorer;
4. semantic consequence mutation tests;
5. exact-byte materialization at all five tiers;
6. proof that B1 historical artifacts are unchanged;
7. fresh per-endpoint output-capability census;
8. fresh pricing census using the admitted per-endpoint maximum output ceilings;
9. explicit owner spend authorization for the resulting 25-cell tranche;
10. exact-SHA governed execution.

## 13. Interpretation boundary

This first B2 tranche is diagnostic, one observation per cell.

It can locate candidate transition regions and compare B1 dilution against B2 information-load behavior at matched byte sizes.

It cannot by itself establish a universal context threshold or smooth causal curve. Any candidate threshold found in B2 requires a later repeated-sample confirmatory campaign around that region.
