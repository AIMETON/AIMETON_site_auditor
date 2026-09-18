# Admin LLM Control Center v0.1

Status: implementation candidate  
Scope: AIMETON Site Auditor admin workspace and runtime LLM policy

## Goal

Move LLM selection and operational parameters out of implicit environment-only behavior into an authenticated administrative control plane without exposing credentials or creating a second provider dispatcher.

The admin workspace controls three independent runtime roles:

| Role | Purpose | Default |
|---|---|---|
| `fast_research` | search-result triage, document/block triage, coverage-wave routing | `routerai-qwen35-9b` / `qwen/qwen3.5-9b` |
| `extraction` | structured company/profile fact extraction | `routerai-current`, phase parameters inherited |
| `reasoning` | KM reasoning, commercial opportunity and synthesis, legacy analysis/chat | `routerai-current`, phase/provider reasoning policy inherited |

The persisted record is `llm.runtime.settings.v1` in the existing runtime SQLite `runtime_meta` store.

## Authority model

The control center is runtime authority for **new LLM calls**. It does not mutate an already dispatched provider request.

A role selects:

- a governed RouterAI profile;
- optional model-id override;
- optional temperature override;
- optional output-token cap;
- optional timeout override;
- output mode: `inherit`, `strict_schema`, or `json_object`;
- reasoning mode: `inherit`, `off`, or `on`;
- optional reasoning effort: `low`, `medium`, `high`, `xhigh`.

`inherit` is deliberately the default for extraction/reasoning parameters. Adding the admin panel therefore does not silently change established phase-specific timeouts, temperatures, response-format choices or provider reasoning behavior.

Fast Research remains explicitly bounded by default: Qwen 3.5 9B, temperature 0, strict schema, reasoning off, 1200 output-token cap and 15 second timeout.

## Provider boundary

v0.1 accepts only profiles registered in the existing RouterAI-backed model-profile registry.

This is intentional:

- the admin UI does not create a second provider/router implementation;
- credentials remain server-side;
- a model ID can still be overridden to another model available through the same RouterAI credential and endpoint;
- direct OpenAI/Qwen/GLM/DeepSeek credentials remain separate observer/benchmark profiles until a common provider execution contract is explicitly adopted.

## Secret handling

The API and UI never return:

- API keys;
- Authorization headers;
- credential environment values;
- raw provider responses;
- raw prompts.

The safe projection contains only profile name, provider name, resolved model id, configuration-ready flag and non-secret execution parameters.

## Admin API

### GET `/api/admin/llm-settings`

Returns:

- persisted three-role settings;
- safe RouterAI profile catalog;
- safe resolved runtime descriptors.

Requires admin authentication.

### PUT `/api/admin/llm-settings`

Persists all three role settings atomically together with:

- admin actor id;
- timestamp;
- required change reason.

Requires admin authentication and CSRF.

### POST `/api/admin/llm-settings/test`

Runs one short live provider capability probe against the **unsaved values currently entered in the form**.

The probe validates that the selected profile/model can return the requested JSON shape and reports only sanitized telemetry:

- resolved model;
- latency;
- finish reason;
- structured-output validation state;
- prompt/completion/total token counts when reported;
- sanitized error code.

The probe is a real provider call and may incur provider usage. It is only initiated by an explicit admin button action.

## UI

The `LLM Control Center` panel in `/admin/workspace` provides:

- role switcher;
- registered RouterAI profile selector;
- model-id override;
- temperature;
- output-token cap;
- timeout;
- structured output mode;
- reasoning mode and effort;
- resolved runtime state;
- live model probe;
- required change reason;
- profile catalog with credential/configuration readiness.

Unsaved edits are retained independently while switching among the three role tabs.

## Runtime integration

The settings are consumed by:

- `app/fast_research_model.py`;
- `app/routerai_strict_request.py`;
- `app/routerai_split_synthesis.py`;
- legacy `app/llm.py` analysis/chat paths.

Phase safety remains stronger than the admin default where explicitly required. For example, a phase that explicitly disables reasoning continues to disable it even if the role default is `on`.

Output-token settings act as caps for bounded split phases rather than expanding schema-specific phase limits.

## Fail-safe behavior

- unknown profile: reject save;
- direct-provider profile: reject save in v0.1;
- missing credential/model: expose `configured=false`, do not leak why through secret material;
- invalid CSRF: reject mutation/test;
- invalid persisted record: fall back to product defaults rather than executing malformed policy;
- live probe failure: report sanitized error without saving anything.

## Acceptance criteria

- admin can independently select Fast Research, Extraction and Reasoning models;
- model selection affects new runtime calls;
- default rollout preserves previous phase behavior;
- Qwen 3.5 9B remains the default cheap triage model;
- environment-selected `ROUTERAI_MODEL` remains the default primary model through `routerai-current`;
- model-id override works without changing credentials;
- no secret appears in GET/PUT/test responses or admin JavaScript;
- probe tests unsaved form state;
- CSRF protects save and test operations;
- settings survive process restart through runtime SQLite.
