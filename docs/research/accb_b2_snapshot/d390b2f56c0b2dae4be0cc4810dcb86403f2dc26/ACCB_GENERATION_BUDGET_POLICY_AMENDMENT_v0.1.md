# ACCB Generation Budget Policy Amendment v0.1

Status: **FROZEN METHODOLOGY AMENDMENT**  
Date: 2026-09-08

## 1. Finding

The completed Layer B diagnostic used a global generation ceiling of `8192` tokens.

RouterAI activity evidence for the DeepSeek V4 Pro medium cell shows that both the original attempt and the governed recovery consumed:

- total output tokens: `8192`;
- reasoning tokens: `8192`;
- visible final-answer tokens: `0`.

The harness then observed an empty final `message.content`.

Therefore the previous global 8192-token ceiling was an experimental confounder for reasoning models: it could terminate reasoning before the model had any budget left to emit the required scored JSON.

## 2. New invariant

**ACCB MUST NOT use a low fixed global output-token ceiling such as 8192 for scored cognitive-integrity experiments.**

For every future scored cell:

1. a fresh no-paid capability census MUST identify the selected exact endpoint;
2. the endpoint MUST advertise a concrete maximum output/completion token capacity;
3. the request generation ceiling MUST be set to that endpoint-advertised maximum, unless a later preregistered model-specific ceiling is demonstrably non-binding;
4. if the endpoint maximum is absent, ambiguous or below the experiment's admitted minimum, execution MUST fail closed;
5. no provider call is allowed merely to discover the limit.

The output ceiling is therefore a **per-endpoint execution capability**, not a shared experimental independent variable.

## 3. Reasoning models

For endpoints exposing reasoning/thinking tokens:

- reasoning tokens and visible final-answer tokens MUST be accounted separately when the provider supplies them;
- `finish_reason` / incomplete status MUST be retained as sanitized evidence;
- an output-cap hit MUST be classified as `OUTPUT_BUDGET_EXHAUSTED` or a more specific integration disposition;
- output-cap exhaustion MUST NOT be scored as ACI=0;
- raw chain-of-thought/reasoning content MUST NOT be retained.

## 4. Final-answer contract

The scored answer remains deliberately compact structured JSON.

A short final-answer schema does **not** justify a short total output ceiling for a reasoning model because the provider may account internal reasoning inside the same generation budget.

## 5. Cost governance

Using the endpoint maximum as the admitted ceiling can increase the conservative cost guard substantially.

Therefore:

- the no-paid census MUST price the full admitted ceiling;
- the paid tranche MUST remain blocked if the resulting conservative guard exceeds the owner-authorized budget;
- budget control MUST be solved through explicit authorization or experimental redesign, never by silently reducing reasoning/output capacity until it becomes a cognition confounder.

## 6. Historical evidence

The completed Layer B run is not rewritten.

Its DeepSeek medium cell remains unscored, with the more precise post-hoc explanation:

`OUTPUT_BUDGET_EXHAUSTED_BY_REASONING_UNDER_LEGACY_8192_CAP`

The historical 8192 ceiling remains part of the immutable execution receipt and is explicitly deprecated for future ACCB scored runs.

## 7. Scope

This amendment applies to all future ACCB cognitive-integrity scored executions, including any replacement Information Load scaling experiment.

It does not authorize any paid provider generation.
