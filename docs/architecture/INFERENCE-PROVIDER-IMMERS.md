# Inference Providers / Immers.cloud

Status: pilot integration  
Scope: AIMETON Site Auditor runtime LLM provider layer

## Purpose

Immers.cloud is registered as a provider-neutral OpenAI-compatible runtime profile. Site Audit business logic does not contain Immers credentials, endpoints or model IDs.

The first profile is `immers-primary`. Its endpoint, credential and default model are resolved from:

- `IMMERS_BASE_URL`;
- `IMMERS_API_KEY`;
- `IMMERS_DEFAULT_MODEL`.

Secrets are never returned by the admin API.

## Model allowlist and capabilities

The routable model registry is `config/inference_provider_registry.json`. It is intentionally separate from any provider catalog or marketing model list.

A model is runtime-eligible only when it is enabled in this registry. The pilot allowlist contains `DeepSeek-V4-Flash-0731`; changing model IDs or capability/pricing metadata is a configuration change rather than a business-logic change.

Capability values can be `true`, `false` or `null` (unknown). Unknown is not treated as supported.

## Admin LLM Control Center

`/admin/workspace` exposes all governed runtime inference profiles in the existing LLM Control Center. Immers can be selected independently for `fast_research`, `extraction` or `reasoning`.

The safe catalog exposes provider, model, configuration readiness, allowlist state and capability metadata. It never exposes credentials or endpoint URLs.

The existing `POST /api/admin/llm-settings/test` capability probe works with an unsaved Immers role selection exactly like other OpenAI-compatible profiles.

## Runtime boundary

`app.llm_runtime_settings.resolve_llm_runtime()` resolves both legacy RouterAI profiles and provider-neutral registry profiles. Existing RouterAI defaults are unchanged.

Direct legacy observer profiles remain non-routable unless explicitly promoted into the runtime provider registry. This prevents an observer/benchmark credential from becoming production execution authority by accident.

## Timeouts and retries

The Immers registry profile carries provider defaults:

- total timeout: 180 seconds;
- connect timeout: 20 seconds;
- max retries: 2.

Role-level admin timeout can override the total runtime timeout. Retry policy is metadata until the provider-neutral execution adapter is completed; callers must not infer side-effect-safe retries from the numeric setting alone.

## Cost metadata

`price_input`, `price_output` and `price_cached_input` are configuration fields. Unknown prices remain `null`; they must never be interpreted as zero cost.

## Qualification state

This change establishes configuration/runtime/admin selection only. A provider is not qualified merely because it appears as configured.

Acceptance still requires live qualification against configured Immers credentials: completion, long prompt, JSON/schema behavior, real extraction/company-analysis tasks, timeout classification, retry-safe fallback, concurrency, and at least one full Site Audit. Results must be captured as project evidence before Immers is considered production-qualified.

## Security

- credentials come only from environment/secret storage;
- Authorization headers and endpoint URLs are not projected through admin responses;
- raw prompts/completions are not added to admin diagnostics;
- Russian hosting/location is not treated as proof of legal compliance with personal-data requirements.
