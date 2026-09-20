## Identity title-segment ownership fix — 2026-09-20

Live exact-SHA regression on `c2337a94befd6e22caa647a4dcc94aec1d812abf` completed successfully and proved the remaining identity failure is inside ownership scoring: DaData checked two candidates, but `identity_resolution_state=unresolved` and no identifier was selected.

The compact-name matcher itself is valid. The upstream caller was passing only the title prefix before the first `|` or em dash as `company_hint`. On SEO-style titles where the descriptive prefix comes first and the brand is in a later segment, the resolver never saw the brand at all. Active correction `fix/identity-title-segment-matching` keeps the existing search hint for search planning but passes the full first-party page title to identity resolution. The DaData matcher evaluates exact compact forms for each meaningful title segment independently, preserving punctuation/spacing tolerance without fuzzy substring matching. Multiple matching brand segments remain ambiguous rather than being auto-promoted.

Operational invariant clarified: deep-research quality is primary. Mission-level time is an optimization parameter, not a reason to discard a valid accumulated profile. Per-provider/per-operation deadlines may protect against a hung call, but must degrade/retry locally and preserve already collected evidence; they must not destroy the mission result.

## Pre-synthesis identity progress observability — 2026-09-20

Two exact-SHA live attempts on `804cbe90fc18ba56adabf8c941b2dd6611b17774` passed Stage/model preflight but stalled in `llm_synthesis_running` before the final `result` object was assembled. This prevents black-box acceptance of the already-computed DaData ownership result because identity candidate enrichment occurs before RouterAI synthesis, while the existing sanitized workflow only publishes final result fields plus generic research accounting.

Active correction `fix/identity-progress-checkpoint` does not change audit semantics. It records a safe pre-synthesis identity checkpoint in the existing `ResearchControl` snapshot: candidate count, registry-mirror resolution state, whether an owner was selected, and selected INN/OGRN. The existing status/recovery path exposes only snapshot-approved fields, and the Aleks Dent live acceptance workflow adds those fields to sanitized research accounting. No prompts, cookies, provider payloads or private account data are added.

Acceptance requires the next exact-SHA live run to expose identity progress even if RouterAI synthesis stalls. For the current Aleks Dent case, the expected pre-synthesis checkpoint is two checked candidates, registry-mirror verified ownership, and selected INN `2462215501` plus OGRN `1112468013030`.

## Stage materialization resilience follow-up — 2026-09-20

Main-server telemetry is now available machine-to-machine through the canonical AIMETON Operations Board live readback. The authoritative local snapshot is `/var/lib/aimeton/operations-board/snapshot.json`, served read-only at `http://localhost:8787/snapshot.json`; command route `/read-operations-board-live <infra-main-sha>` on infrastructure issue #419 produced successful readback run `35497554264` with overall GREEN, 12/12 runners online, queue=0, offline=0, unknown=0.

That telemetry disproved runner-capacity explanations for the current Stage failures. Deploy of Site Auditor SHA `8b2ddd2409901b125fde4d76d2ed6c1c3f71cd67` succeeded on attempt 3, while DaData, persistence and convergence gates failed independently on single-shot `git fetch` calls to github.com. Live Stage itself reported the exact deployed bundle healthy and DaData runtime state active.

Active correction `fix/stage-materialization-bounded-retry` applies the already-canonical infrastructure materialization pattern to Stage workflows: reuse an already-present exact commit when available, otherwise perform up to five authenticated bounded fetch attempts, then require exact `git rev-parse HEAD` equality. A regression test protects this contract across deploy, DaData, persistence, auth and convergence workflows.

## DaData compact legal-name matching follow-up — 2026-09-20

Exact-SHA live regression on `d86b688a042b4cf0307c943876bb90434e67e95f` proved that first-party registry candidate retention now works: `dadata_identifier_candidates_checked=2`, and DaData returned one verified registry-mirror record for INN `2462215501` and OGRN `1112468013030`. The remaining identity blocker is downstream ownership scoring, not candidate discovery or provider availability.

The registry record represents the legal name as `ООО "АЛЕКСДЕНТ"`, while the audit target hint is `Алекс Дент`. Existing ownership scoring only intersects lexical tokens, producing `{алекс, дент}` versus `{алексдент}` and therefore `best_score=0`; identity stays unresolved even though both identifiers resolve to the same legal entity.

Active correction `fix/dadata-compact-legal-name-match` adds an exact compact-name form that removes legal-form/generic tokens plus spacing and punctuation differences without fuzzy substring matching. This should accept `Алекс Дент` ↔ `АлексДент` while refusing broader names such as `АльфаДент Сервис` for target `Альфа Дент`. Neutral tests cover both the positive compact-equivalence and negative substring case.

The same live run also confirmed the upstream repair: two candidates reached DaData. Acceptance now requires promotion of the same verified entity to the audit identity, preserving the FNS authority gate and excluding foreign identifiers.

## First-party registry candidate retention follow-up — 2026-09-20

Owner-authorized exact-SHA regression `35487878111` on `d3f26b993852e795804f477710a986cc0c7249e7` completed successfully but still reported `dadata_identifier_candidates_checked=0`, `deterministic_first_party_identifier_count=0`, provider state `not_attempted_no_identifier`, and identity `unresolved`. Commercial support improved from `unsupported` to `weak` and the opportunity now carries `source_ids=["S1"]`, but legal identity remains absent.

The second live failure localizes the defect upstream of candidate aggregation. First-party legal identifiers are only visible downstream when block triage keeps them or the stricter target-scoped identity selector forces them into evidence. Therefore a valid labelled INN/OGRN can disappear before DaData if entity triage rejects/omits the block and target context is not strong enough.

Active correction `fix/retain-first-party-registry-candidates` introduces a separate ownership-agnostic retention path for safe checksum-valid labelled INN/OGRN pairs on first-party documents. Such blocks are forced into evidence as `unknown/registry` candidates, not as target identity. Deterministic promotion remains restricted to the existing target-scoped selector; DaData and later entity resolution decide ownership. Footer/sidebar/related-context identifiers and checksum-invalid values remain excluded.

Neutral tests cover checksum validation, footer exclusion, split label/value retention without target context, and an integration case where semantic triage keeps zero blocks but valid first-party legal identifiers still survive into child evidence.

Live comparison against the original `4c10a64...` baseline: products 112→92, evidence blocks 1651→1651, other 37→49, LLM calls 79→72; commercial support `unsupported→weak`. Identity quality is not accepted and requires another exact-SHA live run after this correction is green, merged and converged.

## Live identity regression follow-up — 2026-09-20

Part of #915. PR #967 merged as `3f449ca3d9e209b13e9a607a033809d987e64dbd`; post-merge Baseline CI, Deploy Stage, DaData configuration, persistence/auth guards and exact-SHA Stage convergence succeeded. Owner-authorized Aleks Dent deep regression run `35481360011` completed successfully at the workflow/runtime level, but failed the identity-quality acceptance: `deterministic_first_party_identifier_count=0`, `dadata_identifier_candidates_checked=0`, provider state remained `not_attempted_no_identifier`, INN/OGRN were absent and identity stayed provisional.

The live result falsified the first candidate implementation. Root cause is in candidate discovery, not DaData availability: child evidence was partitioned by target/non-target relation before split labels and numeric values were reconstructed. A structurally adjacent `ИНН:` or `ОГРН:` block and its number therefore disappeared when triage assigned different relation classes to the two blocks.

Active correction `fix/identifier-candidate-cross-relation-split` reconstructs labelled identifiers across adjacent child blocks first, then derives a conservative target prior from the exact blocks spanned by the match. Parent quotes remain excluded when child evidence exists. Unknown/counterparty candidates may be checked by DaData but do not gain target scope. Neutral fixtures cover split relation boundaries and target/non-target scope preservation.

Live comparison versus preceding `4c10a64...`: products 112→107, evidence blocks 1651→1651, other 37→43, LLM calls 79→71. Commercial support remains unsupported with empty source_ids, so this stage is still open. A new exact-SHA live run is required after the correction is green, merged and converged.

## Multi-identifier DaData identity resolution candidate — 2026-09-20

Part of #915. PR #967 changes legal-identity recovery from "pre-filter a single target identifier, then query DaData" to "collect all checksum-valid first-party identifier candidates, enrich them, then resolve ownership". INN/OGRN found in target, affiliate or counterparty first-party blocks remain eligible for the existing DaData registry-mirror lookup. Responses are clustered by legal entity so INN and OGRN for one organization reinforce rather than compete.

DaData remains non-authoritative: a returned organization is promoted to the audited company only when target/name/already-known-identifier evidence produces a unique winner; ties and weak matches keep identity provisional, and FNS authority verification remains open. The implementation is URL/CMS/DOM agnostic. Research status adds `dadata_identifier_candidates_checked` for black-box validation.

Initial PR checks exposed two candidate defects before merge: localized identifier validation used Latin INN/OGRN labels instead of extractor-recognized ИНН/ОГРН, and the PR lacked the governance-required acceptance section/linked Issue. Both are corrected in the active branch. Live Stage acceptance remains pending until PR CI is green, merge is complete and exact-SHA Stage convergence is confirmed.

<!-- User execution integration candidate 2026-09-15T09:20:34.513355+00:00 -->

## Admin LLM Control Center candidate — 2026-09-18

A new authenticated admin control plane now manages LLM runtime roles without exposing credentials.
Three independent roles are configurable: Fast Research, Extraction and Reasoning. The persisted
`llm.runtime.settings.v1` record stores only profile/model selection and non-secret execution
parameters; RouterAI credentials remain environment-owned.

The admin workspace adds profile/model selection, temperature, output-token cap, timeout,
structured-output mode, reasoning inherit/off/on and reasoning effort, plus a short sanitized live
capability probe that tests unsaved form values before saving. Extraction/reasoning parameters use
inherit-by-default semantics so introducing the panel does not silently change established
phase-specific behaviour. Fast Research keeps the bounded Qwen 3.5 9B default.

Runtime integration covers fast evidence/search triage, strict profile extraction, split reasoning,
legacy monolith analysis and chat. Unknown/direct-provider profiles are rejected in v0.1 so the
feature does not create a second provider dispatcher. Save and test calls require admin + CSRF.
See `docs/architecture/ADMIN-LLM-CONTROL-CENTER-V0.1.md`. PR CI and exact-SHA stage admin
acceptance remain pending.


## Large evidence corpus: schedule chunks instead of rejecting coverage

Checkpoint: 2026-09-17T01:01:01.022541+00:00. Part of #915; follows #929 live validation.
On deployed 8b6676f6baa9268c9a2a348f63c8bb823f6ed675, the Selectel run
mission_4228d36e7e604167b320ece9c0035a06 / analysis_aead1caf8da948a2b11ac37f0b82b106
exposed partial_result while running, then completed with a degraded final report:
4068 source/evidence entries, 8 company facts, 27 finished queries, 81 finished
provider calls (8 failed). RouterAI failed after 0.4 seconds; the report records
EvidenceCoverageOverflow. Estimated input was 4,968,872 characters. This is proof
of partial-report delivery, not successful full company synthesis. The public trace
does not distinguish the exact overflow branch; code had both a 16-unit extraction
gate and a large-input monolith gate, and regression tests cover both.

The new scheduling behaviour plans all evidence units. Above the fast-path size,
ordinary extraction uses the existing checkpointed chunk processor, loss-preserving
output schemas, output-truncation subdivision and shared concurrency of four. A
large monolith input routes to split synthesis even when monolith is selected for
small inputs. No prefix trimming, evidence dropping or paid-operation permission
gate is added. Automatic scheduling does not set deep_research consent or widen
the search frontier. Existing controls are reused; calls without one get a locally
scoped accounting control for extraction checkpoints, not a new owned mission.

User-selected deadlines and explicit stop remain effective. Legacy aggregate
deadlines for runs without saved settings still apply: this change removes the
size rejection, not all remaining lifecycle/time constraints. A deadline or provider
failure can still leave incomplete extraction. Full live synthesis of the Selectel
corpus on the new scheduler remains a separate acceptance task. Monetary totals
remain unconfirmed; provider call counts are not invoices.

The large status response was 4,070,180 uncompressed bytes; existing zstd negotiation
transferred 424,919 bytes. No truncation or duplicate compression layer was needed.
Validation: 1488 passed, 1 xfailed, 6 subtests locally. Tests cover late official-text
and external-source facts, complete unit accounting, concurrency and routing away
from an undersized monolith.


## Partial site report survives interruption

Checkpoint: 2026-09-16T16:28:30.112378+00:00. Part of #915, P0-D.
Async site audit now saves a local heuristic report immediately after site acquisition,
before awaiting external enrichment. The existing durable analysis projection gains
an additive nullable partial_result_json column; old databases migrate in place.
REST/MCP shared status exposes partial_result separately from result. Mission/analysis
IDs and final fetched URL are retained; readiness stays preliminary and explicitly
blocks client release with audit_not_completed. The report identifies its stage as
site_acquired and quality as partial, with a visible explanation of incomplete research.

Timeout, requested stop, subsequent error or process restart retain that checkpoint.
A stop observed before final commit cannot publish the completed result. Success still
returns result normally. Failed/interrupted runs do not become completed just because
a partial report exists. UI shows partial reports under a separate analysis-partial
event and heading, retains export/history metadata and stops polling a confirmed
restart interruption. Transient degraded/blocked phases no longer end UI polling.
The initial polling timer is registered before the first awaited poll, avoiding a
new timer after a terminal response has already cleared it.

Scope is the local site-acquisition report, not a merger of all in-flight external
evidence/chunk checkpoints or a reconstruction of raw page text. Failures before
acquisition still have no partial report. Sync company/site responses and durable
provider resume remain outside this slice. No extra provider calls, spending caps
or permission gates are introduced. Tests cover timeout, stop, error, success,
restart, old-schema migration and real workspace partial/completed event rendering.
Full local suite: 1486 passed, 1 xfailed, 6 subtests. Live deployment is checked separately.


## Recovery of research accounting after process restart

Checkpoint: 2026-09-16T06:27:41.834941+00:00. Part of #915, P0-D accounting continuity.
The async analysis identifier and research run identifier were distinct. #923 persisted
spending checkpoints but REST/MCP status read usage only from in-memory controls.
After restart the durable analysis remained visible while its counters disappeared.

New async site/refinement launches persist an immutable analysis-to-run/owner binding
before scheduling provider work. Each research checkpoint atomically also writes a
public status snapshot. LLM dispatch now checkpoints the started call before awaiting
a response, so a crash cannot erase that observed call merely because usage is absent.
REST/MCP's shared status projection reads the latest saved snapshot when live controls
are unavailable; recovery is marked with accounting_recovered and checkpoint time.
UI labels recovered counters and hides the non-functional stop control for such runs.
Readback never resumes providers, recreates consent, changes audit state or implies a
confirmed bill. No spending admission gate is added. Only existing public counter
fields are exposed, not owner IDs, settings payloads or provider contents.

Old unbound runs cannot be reliably associated retrospectively and remain without
recovered counters; no guessed association or invented zero is returned. Missing or
corrupt accounting does not hide the durable analysis status. This is a recovery of
last observed accounting, not billing reconciliation or durable worker resume.
Company async execution, partial reports and complete monetary reconciliation remain
open plan items. Tests cover actual launch binding with/without saved settings, loss
of process dictionaries, interrupted LLM calls, currency estimates, immutable binding,
run isolation and unavailable/corrupt accounting.


## Search spending observations

Checkpoint: 2026-09-15T14:24:16.262759+00:00. Continuation of #915 after #921.
Each dispatched SearchGateway attempt now checkpoints its configured tariff estimate
in the bound research run, including retries and interrupted/failed calls. Decimal
totals retain currencies separately; no FX conversion or invented tariff is used.
Unknown paid pricing has a separate counter. Denied work and cache responses do not
invoke the dispatch hook. The async site-audit UI labels these amounts as search
tariff estimates, not confirmed billing or the total audit cost. Provider LLM billing
remains unknown; monetary_cost=not_reported and monetary threshold status=unknown
remain honest until reconciliation is implemented. This adds observation only, no
new admission gate or user approval step. Tests cover exact decimal accumulation,
separate currencies, unknown pricing, retry costs, cancellation and denied work.


## Owner correction: account for spending, continue execution

Checkpoint: 2026-09-15T14:20:07.784980+00:00. Part of #915; supersedes the monetary/token admission
gates introduced in #918. The owner explicitly provides a debugging budget and
requires accounting and notifications, not automatic restrictions on paid debugging.

New audit snapshots use spending_policy=account_only. Saved cost/token thresholds
(including legacy fields named limit) are advisory planning values. Their presence,
legacy pause preferences and unknown price do not block audit launch or subsequent
LLM calls. Effective policy explicitly reports both monetary/token enforcement=false,
notify_continue and allow_unpriced; requested revisions remain immutable.

Token threshold status uses observed token totals and appears in run checkpoints;
the async audit UI reports reaching a threshold while continuing. Missing usage
remains unknown. Monetary totals are still not_reported, monetary threshold status
is unknown; this change does not claim complete billing reconciliation. Completing
provider monetary accounting remains work to do, never a prerequisite for debugging.
The separate reservation-ledger foundation is not activated as an audit gate.
User-selected timeouts, explicit stop, ownership/CSRF and provider access controls
retain their existing purpose. No new spending permission prompt is introduced.

Validation: regression coverage includes authenticated audit acquisition with saved
cost/token planning values, a subsequent LLM call after crossing the token threshold,
legacy unknown-price pause compatibility, immutable effective policy and UI launch.

Local verification: 1471 tests passed, 1 xfailed, 6 subtests.
Next slice after merged/deployed #917 connects saved per-service preferences to
site/company audit launches, immutable effective snapshots, wall-clock deadlines,
search retries and settings UI. Unsupported monetary/token/pause guarantees block
before execution. See docs/architecture/USER_RESEARCH_EXECUTION_V0.1.md. Company
async lifecycle, complete partial-result recovery and tariff enforcement remain open.

<!-- Research settings implementation checkpoint 2026-09-15T07:56:45.410907+00:00 -->
P0-B/C draft #917 now includes durable reservation/claim/settlement accounting and
missing per-run usage hooks in legacy synthesis and advisory LLM paths. Full local
suite: 1453 passed, 1 xfailed, 6 subtests. Provider integration, authoritative tariff
bounds, time guards, settings UI and activation remain open; no production budget
cap is claimed. See docs/architecture/USER_RESEARCH_SETTINGS_V0.1.md. #916 stage
deploy 34925242984 is confirmed successful.

# AIMETON Site Auditor · Current Status

_Last updated: 2026-07-30_

## Audit recovery continuation — 2026-09-15T03:33:54.995917+00:00

PR #916 merged as e4941e5c052842bd9600f941a936160fd44e71bb after all three PR checks passed. Post-merge Baseline CI 34925159315 succeeded. Stage health independently returned this exact deployment SHA on 2026-09-15; this is deployment identity evidence, not a real-company recall acceptance.

Next P0-B draft adds server preference schema/revisions/snapshots and isolated authenticated API, with 16 focused tests passing. Router is not mounted: UI, effective policy and enforcement remain required before delivery. Details: [user settings](docs/architecture/USER_RESEARCH_SETTINGS_V0.1.md). Full mission #915 remains open.

## Audit quality recovery / #915 — 2026-09-15T02:39:46Z

Owner requested a detailed plan and implementation start. Plan: [Audit Mission Quality](docs/roadmap/AUDIT_MISSION_QUALITY_PLAN_2026-09-15.md). P0-A candidate recovers overlooked DOM text in both extraction paths and splits oversized blocks without dropping tails. Six regression cases failed on base and now pass. Full suite: 1404 passed, 1 xfailed, 6 subtests before final equal-chunk test; final focused suite: 25 passed. Deployment/live acceptance remains open. User-owned persistent time/cost settings and their runtime enforcement are P0-B/C, not shipped here. Strict domain fallback remains protected; guarded dynamic support is a separate planned step.

## Hunter stop-responsiveness corrective candidate — 2026-09-14T02:54:18Z

PR #907 merged as `3377b758fd48763a83bbcd9a407c417353434f3c`.
Post-merge Baseline CI run 34800345992 and Deploy Stage run 34800405291
succeeded; `/api/health` reported the exact merge SHA. A live Russia/dentistry
run remained active after the rejected 45-second boundary: at 74.3 seconds all
20 search directions had completed and candidate inspection had begun (`0/100`).
Continuous progress and absence of automatic aggregate truncation are confirmed.

A reduced live run accepted explicit stop and returned `stopped` in 7.79 seconds,
but two candidate-pool runs did not return the stop response within 30 and 120
seconds. Full-load stop responsiveness is therefore RED. The corrective PR #908
candidate moves synchronous DNS validation, HTML parsing and Hunter heuristic
analysis off the asyncio event loop so status/stop requests remain serviceable
during candidate work. Focused scraper/Hunter/runtime suite: 28 passed. CI and
exact-SHA stage revalidation remain open.

## Hunter continuous-search follow-up — 2026-09-14T02:03:12Z

PR #906 merged and deployed as `2aeae347dc2e0768e02018aa22a4ae9dc9dc070b`;
the former dentistry HTTP 500 no longer reproduced. A full request exceeded 150
seconds because candidate deep inspection lacked an aggregate phase deadline.
The first PR #907 candidate proposed a 45-second aggregate deadline. The owner
rejected that direction because it sacrificed completeness. The superseding
candidate runs Hunter in the background without an aggregate search/candidate
deadline, reports elapsed time and query/candidate progress, and exposes an
explicit user stop action. A stop returns only completed candidate checks and
does not invent shallow replacements for cancelled work. The legacy synchronous
`POST /api/hunt` remains compatible. Focused Hunter/API/UI suite: 31 passed;
the full local suite is 1386 passed, 1 expected xfail and 6 subtests passed.
Implementation SHA `153126855af10e8a45f2bf2bd193316e6c25892a` passed Baseline CI
run 34798185817 and Acceptance Governance run 34798200106. The run controller
is process-local; restart recovery is not yet implemented. Merge/deploy decision
and exact-SHA stage acceptance remain open.

## Hunter HTTP 500 corrective candidate — 2026-09-13T18:27:27Z

Stage reproduced HTTP 500 for Auto client search with industry `стоматология`.
PR #906 now initializes a missing persisted standard search policy at startup and
contains per-direction/per-candidate failures. Exact exception type remains
provisional until corrected stage validation. Incident evidence:
[`HUNTER-HTTP-500-2026-09-13`](docs/incidents/HUNTER-HTTP-500-2026-09-13.md).

## Large-document preflight candidate — 2026-09-13T14:01:41Z

PR #906 adds relevance screening before full extraction for acquired documents
>=48,000 characters. Headings/beginning are inspected first; exclusions require
confirmation using middle/end/identity excerpts. Errors and uncertainty retain
content. Decisions and exclusions are visible in the preliminary report.
Contract: [Document preflight](docs/architecture/DOCUMENT-PREFLIGHT-V0.1.md).
This saves LLM extraction work; partial binary downloads and live recall validation
are not implemented/confirmed.

## Opt-in deep research candidate — 2026-09-13T13:30:00Z

PR #906 now includes explicit per-run LLM budget consent in audit and chat,
uncapped aggregate extraction/acquisition, usage counters and owner-scoped stop
with partial results. Standard mode remains the default. Contract and limitations:
[`AUDIT-DEEP-RESEARCH-V0.1`](docs/architecture/AUDIT-DEEP-RESEARCH-V0.1.md).
Candidate only: deployment, paid-provider smoke and automatic restart continuation
are not confirmed.

## Audit profile recovery candidate — 2026-09-13T08:12:12Z

Implementation candidate on base `a9d08d74c2fa955d5299c2b7e698a387bb352d92`:
shared verified audit/Hunter search policy, full fetched-block retention,
site-derived registry pivots, bounded dialogue refinement and versioned
preliminary profile. Local suite: 1362 passed, 1 xfailed; app import/JS syntax pass.
No deployment or real-company quality recovery is claimed.

Contract, evidence and open acceptance:
[`AUDIT-RESEARCH-PROFILE-LOOP-V0.1`](docs/architecture/AUDIT-RESEARCH-PROFILE-LOOP-V0.1.md).
The older operational snapshots below remain historical evidence.

## Активный контур Search Recovery

- `SR-G0 / #81` слит в `main` через PR
  [#79](https://github.com/Dimar4713/AIMETON_site_auditor/pull/79);
  merge SHA `1f3f2d6bbe9fc350dffcb58accd539ccf70f1a8e`.
- `MissionReleaseControl` и fail-closed Report Gate являются фактом `main`, но
  не доказывают восстановление самого поиска.
- `SR-G1 / #82` слит через PR
  [#91](https://github.com/Dimar4713/AIMETON_site_auditor/pull/91) и
  развёрнут на stage: версия `0.12.0`, deployment SHA
  `5b6a65d0d26b64087b0e90d3f040668c7d7cdfce`.
- Live provider readiness: SearXNG — `active`, `ready=true`; Yandex и Tavily —
  `not_configured`, `ready=false`; `secrets_exposed=false`.
- Кодировки, полнота смысловых областей, URL/redirect/canonical identity и
  operational readiness providers являются фактом `main` и stage.
- Регистрационные данные платных providers потребуются только после зелёного
  CI `#82`, перед live-проверкой stage. Значения секретов не передаются через
  чат, Issues, PR, код или логи.
- `SA-SR-02 / #83`, Mission Orchestrator, слит PR `#92` в `main`: code merge
  SHA `7b6602c98e02a982716112b34d225e66cebc4dad`, pre-merge Baseline CI
  `#30470034095` — success. Live stage `/api/health` подтверждает версию
  `0.13.0` и exact deployment SHA `7b6602c98e02a982716112b34d225e66cebc4dad`.
  Yandex/Tavily остаются `not_configured`, SearXNG — `active`.
- Bootstrap-срез `SA-SR-04 / #85` слит PR `#93` и развёрнут на stage:
  версия `0.14.0`, exact deployment SHA
  `80ac7c045e4300c991aa167208786596dd53c2b0`, pre-merge Baseline CI
  `#30471814480` — success. Вся `#85` остаётся открытой; следующий активный
  шаг — provisional Entity Resolution `#84` на provenance-bearing signals.
- Provisional-срез `SA-SR-03 / #84` слит PR `#99` и развёрнут на stage:
  версия `0.15.0`, merge/deployment SHA
  `de9f864b10b1cb1c340f0d8429273e4e908d650a`; PR Baseline CI
  `#30503294900`, main Baseline CI `#30503376185` и Deploy Stage
  `#30503427439` — success. Live HTTP-цикл подтвердил
  `plan → resolve_identity → identity_history`: state `provisional`, одна
  ревизия, `accepted_identifier_links=[]`, следующий `query_provider` остался
  только candidate и не выполнялся. Registry verification, accepted identity
  links, human review и Benchmark identity остаются следующими срезами `#84`.
- После добавления repository secret `TAVILY_TOKEN` подготовлен локальный
  candidate следующего среза `#84`: `query_provider → DiscoveryHint →
  fetch_document → Evidence Guard → accepted_identifier_links → targeted
  crawl candidate`. PR `#101` слит и Deploy Stage `#30516485317` подтверждён:
  stage работает на `0.16.0 / 1ed2376…`, Tavily имеет `state=active` и
  `ready=true`; сам secret и его значение в документации, диагностике и логах
  не фиксируются.
- Первый live-проход на страницах Selectel, Sendy и БСК остановился до Tavily:
  Entity Resolution корректно вернул conflict, но причиной оказалась ложная
  атрибуция вариантов имени и банковских контрагентов. Исправление слито PR
  `#102` и развёрнуто как `0.16.1 / 5d7cb748…`; Baseline CI
  `#30521415347` и Deploy Stage `#30521495457` — success.
- Повторный live HTTP-цикл на БСК подтвердил provisional ООО «Анатомика»,
  корректные ИНН/ОГРН, locator-bound accepted links, две identity revisions и
  targeted crawl candidate. Tavily Basic был вызван один раз (`quota 10 → 9`),
  но не вернул usable result; SearXNG fallback дал пять discovery hints.
  Продолжение на cache завершило Evidence Guard с дополнительной стоимостью
  `$0`. Official registry verification остаётся открытым дефицитом.
- `Identity Benchmark-5 v0.1` слит PR `#105` и развёрнут как
  `0.16.2 / 45b38b81079bd13a1690bf84283ebeb7b101087f`; PR Baseline CI
  `#30540392356`, main Baseline CI `#30540507493` и Deploy Stage
  `#30540595302` — success. Пять замороженных Golden-5 случаев проходят
  `sanitized HTML → signals → provisional identity` без сети, provider и LLM.
  Definition-list `dt/dd` теперь входит в document blocks. `#84` остаётся
  открытой для official registry verification и human review.

## Текущее положение

Ниже сохранён исторический эксплуатационный baseline SA-01/SA-02. Он не
заменяет активный контур Search Recovery выше.

**Исторически завершённая фаза:** SA-01 — стабилизация поискового и MCP-контура.

**Завершено:** SA-01.1–SA-01.8 / Issues #9–#16; Epic #7 закрыт.

**Завершённый эксплуатационный шаг:** SA-02.1 / Issue #27 — автоматизация deployment `main → VPS stage`.

**Активный следующий шаг:** SA-02.2 / Issue #28 — OpenStack-контур управления инфраструктурой immers.cloud.

## Подтверждённый stage

- `/api/health`: `200 OK`, версия `0.6.1`;
- `/mcp`: `307 Temporary Redirect`, `Location: /mcp/`;
- `/mcp/`: без `421`, MCP initialize отвечает `200 OK`;
- первый автоматический deployment выполнен для merge SHA `cf55c5c808a92524a1e85846b13b07b202dfe8af`;
- self-hosted runner `aimeton-site-auditor-stage` зарегистрирован в `Dimar4713/AIMETON_site_auditor` и работает как systemd service;
- GitHub Actions `Deploy Stage` завершён успешно.

## SA-02.1 — завершено

Рабочий контур:

```text
Baseline CI success on main
  → Deploy Stage workflow
  → self-hosted runner /home/ubuntu/actions-runner-site-auditor
  → exact commit checkout
  → transactional app-source switch
  → docker compose build/up
  → health + MCP smoke
  → rollback on failure
  → deployment evidence artifact
```

Подтверждено:

- deployment запускается после успешного CI для `main`;
- разворачивается полный commit SHA;
- bundle формируется во временном каталоге и валидируется;
- предыдущий `app-source` сохраняется;
- переключение выполняется атомарно;
- Docker service пересобирается и пересоздаётся;
- ожидается состояние `healthy`;
- smoke проверяет `/api/health`, относительный `/mcp → /mcp/` и MCP initialize;
- при ошибке выполняется rollback;
- SHA и evidence сохраняются;
- ручное восстановление задокументировано;
- rollback-транзакция проверена воспроизводимым тестом с искусственно сорванным smoke: предыдущий bundle и SHA восстанавливаются, неуспешный bundle сохраняется в `app-source.failed.*`;
- rollback evidence закреплён тестом `tests/test_deploy_stage_rollback.py`, итоговый commit `233e39bf0b4aa6266526d847e7fcbbd832505792`.

## SA-02.2 — активный следующий слой

```text
AIMETON control
  → OpenStack API immers.cloud
  → Keystone / Nova / Neutron / Cinder / Glance
  → VM, network, volumes, snapshots, recovery
```

OpenStack API не заменяет выполнение команд внутри Ubuntu. Внутренний deployment-контур SA-02.1 остаётся отдельным и завершённым.

## Оперативное управление

Состояние синхронизируется через `CURRENT_STATUS.md`, GitHub Issues, Pull Requests, Actions runs, deployment evidence и stage smoke results.
