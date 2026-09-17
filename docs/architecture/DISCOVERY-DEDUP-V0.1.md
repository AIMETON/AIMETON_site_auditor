# Discovery Dedup v0.1

Part of the audit-quality recovery after #931/#932.

## Invariants

1. Discovery uniqueness is based on canonical URL, not the literal search-result URL.
2. Tracking-only variants (`utm_*`, `gclid`, `fbclid`, `yclid`, `_openstat`) do not create additional candidates.
3. A degraded SearchGateway state is observability, not proof that returned results are unusable.
4. Relaxed fallback is issued only when the exact query returned no results. Additional search for missing coverage is the responsibility of the progressive coverage controller, not an automatic duplicate wave.
5. Search snippets remain discovery hints and never become evidence through deduplication.

## Why

The Aleks Dent regression showed that a productive but degraded exact wave could trigger an additional relaxed wave, while URL variants could enter the candidate pool as separate documents. Both behaviours amplify downstream fetch, triage and LLM work without adding independent evidence.

## Telemetry

The collector records a bounded note with the number of canonical URL duplicates removed. Search diagnostics remain unchanged and continue to report degraded provider state when applicable.

## Next boundary

Canonical discovery dedup happens before fetch. A separate acquisition-layer change must also deduplicate resolved final URLs and normalized document digests after fetch, because redirects and mirrored content cannot be known from discovery metadata alone.
