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
