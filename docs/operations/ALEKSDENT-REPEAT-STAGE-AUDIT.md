# Aleks Dent repeat Stage audit

Status: operational acceptance route for a controlled regression repeat.

Target: `https://aleksdent24.ru/`.

Purpose: repeat the site audit that previously produced low-quality entity extraction after the evidence-control-plane repairs and after the Extraction LLM role was switched through the Admin LLM Control Center.

Execution contract:

- command: `/audit-aleksdent-stage <exact-stage-sha>`;
- authorized command issue: #293;
- workflow: `.github/workflows/audit-competitor-services-realty-stage.yml`;
- target URL is fixed in the command router and cannot be supplied from the issue comment;
- execution uses the currently deployed Stage `/api/analyze` implementation;
- the workflow verifies the exact deployed SHA and healthy auditor before starting;
- evidence is sanitized before publication to Issue #293;
- raw prompts, provider payloads, credentials, cookies and authorization material are excluded.

Cost authority:

The route can invoke configured search/LLM providers and therefore may incur provider usage. It is invoked only through the owner-only command router. The repeat requested on 2026-09-19 is explicitly owner-authorized for one live Aleks Dent regression run.

Acceptance focus:

Compare the new result with the frozen bad-run evidence for Aleks Dent, especially entity contamination, founders/executives/owners, `other` facts, source duplication, completeness/reasoning state, document/source counts, and LLM/search usage telemetry when available.


## 2026-09-19 authenticated deep-research correction

The first governed repeat used the legacy synchronous `/api/analyze` route without an authenticated deep-research request. It was useful as a diagnostic, but it was not equivalent to the original UI deep-research failure: its result reported `search_progressive_enabled=false`, 20 executed queries and a `profile_identity_core / OutputTruncated` extraction failure.

The equivalent regression path is therefore separate:

- command: `/audit-aleksdent-deep-stage <exact-stage-sha>`;
- workflow: `.github/workflows/audit-aleksdent-deep-research-stage.yml`;
- Stage bootstrap admin authenticates through the normal login endpoint; credentials and cookies are never published;
- before any research spend, `GET /api/admin/llm-settings` must resolve Extraction to `~deepseek/deepseek-v4-flash-latest`;
- the analysis starts through `POST /api/analyze/start` with `deep_research=true` and `unlimited_llm_budget=true`;
- the workflow has a 12-minute polling window and requests cooperative stop if it has not reached a terminal state;
- published evidence is a bounded sanitized comparison projection, not the raw provider response.

The additional deep run is part of the same owner-requested regression validation: the prior legacy-mode run established a useful failure signal but did not satisfy the requested same-mode comparison.
