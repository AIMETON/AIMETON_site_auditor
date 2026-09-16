# PD-DATA-FLOW-INVENTORY-V0.1

**Status:** initial evidence-backed inventory  
**Date:** 2026-09-16  
**Issue:** #925 `PD-COMPLIANCE-01 [P0]`  
**Scope:** AIMETON Site Auditor repository; technical facts only.  

> This document is not a legal opinion and does not assert the current status of AIMETON notifications to Roskomnadzor. Legal/operator facts require owner/legal evidence.

## 1. Purpose

Create a reproducible inventory of data that may identify or be linkable to a natural person, and of every external recipient that can receive such data. The inventory is the technical input for the 152-FZ/RKN compliance gate and for later remediation PRs.

## 2. Confirmed first-party data stores and identifiers

| Surface | Confirmed data | Storage / transport | Evidence | Initial classification |
|---|---|---|---|---|
| Local auth | `username`, password-derived hash, user id, role, active state | SQLite (`AIMETON_AUTH_DB`, default `data/auth.sqlite3`) | `app/auth_api.py`, `app/admin_users.py`, auth repository | user-linked data; password itself is accepted only for authentication/reset and must never be logged |
| Session | `aimeton_session` token | Secure + HttpOnly + SameSite=Strict cookie; server-side session repository | `app/auth_api.py` | strictly necessary authentication identifier |
| CSRF | `aimeton_csrf` token | Secure + SameSite=Strict cookie; mirrored in `X-CSRF-Token` header | `app/auth_api.py` | strictly necessary security identifier |
| Admin audit | actor id, target user id, action, free-text reason, result, timestamp | SQLite table `auth_audit_events` | `app/admin_users.py` | user-linked audit data; free-text `reason` may accidentally contain personal data and needs retention/redaction policy |
| Research accounting | owner id, analysis/run ids, token counters, configured search tariff estimates, checkpoint time | Existing runtime SQLite: `research_run_checkpoints`, `research_analysis_runs` | `app/research_control.py`; async analysis status recovery | Internal association; public status exposes existing counter fields only, not owner id/settings/raw provider payloads. No new egress destination. Introduced 2026-09-16; retention/deletion follows the runtime lifecycle, dedicated policy remains open. |
| Site analysis input | target URL, page title, extracted page text | runtime / downstream analysis | `app/llm.py` | page text may contain personal data of third parties |
| OSINT evidence | external source snippets/content including company contacts, founders, executives and other persons | runtime / evidence structures / downstream analysis | `app/llm.py` prompt and `external_sources` projection | can contain third-party personal data |

### Immediate technical note

No code evidence has yet been found that the application itself intentionally reads the visitor IP address or User-Agent into an application database. This is **not** evidence that those values are absent from reverse-proxy, Caddy, hosting, access, WAF or infrastructure logs. Infrastructure logs must be inventoried separately before this row can be closed.

## 3. Confirmed outbound recipients

| Recipient / endpoint | Confirmed outbound payload | Location / cross-border status | Evidence | Gate |
|---|---|---|---|---|
| RouterAI — `ROUTERAI_BASE_URL`, default `https://routerai.ru/api/v1` | prompt containing target URL, title, up to 30,000 chars of official page text and up to ~52,000 chars of serialized external OSINT context | RouterAI operator is a Russian legal entity according to its current public policy, but RouterAI states that prompt text can be forwarded to underlying model providers outside Russia | `app/llm.py`; RouterAI privacy policy/offer | **P0 review required before third-party personal data is included in prompts** |
| Tavily — `https://api.tavily.com/search` | raw `SearchRequest.query`, result limit/settings | Tavily public privacy policy identifies AlphaAI Technologies Inc., New York, USA | `app/search_gateway/providers.py`; Tavily privacy policy | potential cross-border transfer if a query contains personal data; provider can be contract-blocked and must remain fail-closed until classified |
| Yandex Search API — `https://searchapi.api.cloud.yandex.net/v2/web/search` | raw `SearchRequest.query`, search settings and Yandex folder id | Russian-provider endpoint; legal/processing terms still require contract review | `app/search_gateway/providers.py` | classify payload and contract; lower cross-border concern than Tavily, not automatically exempt from 152-FZ duties |
| Self-hosted SearXNG — configured `SEARXNG_BASE_URL` | raw search query, language and optional engine set | depends on deployed endpoint and configured upstream engines | `app/search_gateway/providers.py` | map actual stage/prod host and each enabled upstream engine; self-hosting does not prevent query egress to engines |
| DaData — `https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party` | party lookup identifier/query | Russian endpoint by hostname; exact contract/data-processing terms require review | `app/entity_resolution/dadata.py` | classify whether natural-person data can be queried/returned and retained |

## 4. RouterAI P0 finding

`app/llm.py` currently builds a prompt that explicitly asks the model to extract or reason over, among other fields:

- phones and email;
- founders and executives;
- presumed beneficial owners;
- affiliates and company addresses.

The prompt includes official-page text and serialized OSINT sources. Therefore a normal company-analysis mission can include personal data of third parties in the request sent to RouterAI.

As of 2026-09-16 RouterAI's published privacy policy and offer state, in substance, that:

1. request content may be technically forwarded to the selected underlying model provider, including providers whose servers can be outside Russia;
2. the user is expected not to send third-party personal data through the service and bears responsibility for legal grounds when it does so.

This creates a **technical/contractual compliance gap** that must be resolved before we can claim the current LLM path is safe for production processing of person-level OSINT.

### Safe remediation candidates

Until owner/legal review is complete, engineering can safely prepare these fail-closed controls without changing legal assertions:

- add a provider policy flag forbidding person-level fields in RouterAI payloads;
- add deterministic redaction/pseudonymisation before LLM egress;
- separate company-only facts from person-level OSINT and keep person-level processing local or on an approved route;
- add negative tests proving email/phone/person names cannot leave via a provider classified as disallowed for such data;
- record only payload classes/digests in trace, never raw personal-data payloads.

A decision on which control becomes production policy is an owner/legal gate because it changes product capability and processing model.

## 5. Cookies: current evidence

The confirmed application cookies are authentication/security cookies:

- `aimeton_session` — HttpOnly, Secure by default, SameSite=Strict;
- `aimeton_csrf` — Secure by default, SameSite=Strict, readable by the browser so it can be echoed in the CSRF header.

No repository evidence has yet been found for Google Analytics, `gtag`, Yandex.Metrika or another browser analytics tag in this repository. This must still be verified against the live HTML/network surface and infrastructure injections before marking analytics as absent.

A generic marketing cookie-consent banner should **not** be added merely to look compliant. The UI must describe the actual processing and, where consent is the chosen legal basis, obtain it separately and verifiably.

## 6. Open evidence gates

The following cannot be derived from source code and therefore remain explicit blockers for a final legal declaration:

| Evidence needed | Owner |
|---|---|
| identity/requisites of the personal-data operator for AIMETON | owner/legal |
| RKN operator-notification status and exact declared purposes/categories/systems | owner/legal |
| separate cross-border notification status, countries and recipients | owner/legal |
| approved legal bases for account data, contact data and third-party OSINT | owner/legal |
| approved retention/deletion periods | owner/legal + engineering |
| production/stage reverse-proxy and hosting log fields/retention | infrastructure |
| actual enabled provider set and egress destinations on each environment | infrastructure/runtime evidence |
| public privacy/consent surface on every collection page | web + legal |

## 7. Engineering acceptance for next revision

The next revision should be generated from code/runtime evidence rather than manual assumptions and must include:

- a machine-readable egress inventory (`host`, provider, payload classes, environment, allowed/blocked state);
- a field-level data registry for auth, mission state, logs, reports and exports;
- retention/deletion owners and tests;
- live stage network evidence for client-side trackers/cookies;
- mapping between actual processing and the owner-provided RKN notification evidence;
- a resolved policy for person-level OSINT sent to LLM/search providers.

## 8. External legal/provider references used for this inventory

- 152-FZ Article 9 (separate consent wording in force since 2025-09-01): https://www.consultant.ru/document/cons_doc_LAW_61801/6c94959bc017ac80140621762d2ac59f6006b08c/
- 152-FZ Article 12 (cross-border transfer): https://www.consultant.ru/document/cons_doc_LAW_61801/e4ebbe1780de623c7cf32a59ca82a7bb523a25dd/
- 152-FZ Article 18.1 (policy/publication and organisational measures): https://www.consultant.ru/document/cons_doc_LAW_61801/eeeebe22bf738fd65bb66b95cc278911ae2525ee/
- 152-FZ Article 22 (operator notification): https://www.consultant.ru/document/cons_doc_LAW_61801/d996966e22e1320c9de1ab82d9f6be12c3d9d765/
- RouterAI privacy policy: https://routerai.ru/terms/privacy
- RouterAI offer: https://routerai.ru/terms
- Tavily privacy policy: https://www.tavily.com/privacy

## 9. Non-claims

This inventory deliberately does **not** claim that:

- AIMETON has or has not filed any RKN notification;
- any particular transfer is lawful/unlawful;
- every search query is personal data;
- every technical cookie requires consent;
- a cookie banner by itself establishes compliance.

Those conclusions depend on the real operator, purposes, legal bases, contracts, deployment and notification evidence.