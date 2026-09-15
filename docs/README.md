# Документация AIMETON Site Auditor

## Исследовательские основания

- [Исследовательские основания развития всей поисково-разведывательной системы](research/AIMETON_Site_Auditor_full_system_search_research.md) — история рассуждений и причин архитектурных решений: выводы тестирования KIMI, переход от расширенного поиска к 12 РПТК, фрактальности, многоплоскостности, свойствам мишеней, сценариям, федеративным провайдерам, Evidence/Entity Graph и адаптивному усушению ветвей.
- [Evidence Memory и первый RAG-контур](research/AIMETON_Evidence_Memory_OpenRAG_research.md) — отдельное исследование подсистемы памяти, причины выбора архитектуры и путь от Document RAG к операционной онтологии AIMETON.
- [Semantic Verifier P0 — offline calibration scaffold](research/VERIFIER-P0-OFFLINE.md) — provider-neutral контракт, Golden-5 candidate fixtures, fail-closed границы semantic verifier и условия перехода к live logprob backend.
- [Semantic Verifier P0 — backend capability qualification](research/VERIFIER-BACKEND-CAPABILITY-P0.md) — gate `contract_candidate → runtime_qualified`, RouterAI как первый кандидат и измеримые требования к token-level `logprobs/top_logprobs` без права обхода hard/evidence/policy gates.
- [Semantic Verifier P0 — live Golden-5 calibration](research/VERIFIER-P0-LIVE-CALIBRATION.md) — подтверждённый RouterAI runtime backend, pinned AIMETON fork, budget-aware live harness и измерительный gate перед дальнейшей продуктовой интеграцией.

## Главный план развития

- [Качество аудита и управляемое исполнение миссии](roadmap/AUDIT_MISSION_QUALITY_PLAN_2026-09-15.md) — #915: полнота краулинга, единые runs двух аудитов, пользовательские настройки тайм-аутов и расходов; P0-A в реализации.

- [Audit Research Profile Loop v0.1](architecture/AUDIT-RESEARCH-PROFILE-LOOP-V0.1.md) — кандидат восстановления полноты аудита и уточняющего поиска из диалога; локальные проверки и открытая stage-приёмка.

- [Полный план развития системы поиска, анализа и действий](roadmap/AIMETON_Site_Auditor_full_system_development_plan.md) — целевая архитектура Site Auditor как прикладного Runtime AIMETON: 12 РПТК, федеративный поиск, многоплоскостность, фрактальные ветви, усушение поиска, свойства мишеней, сценарии, Evidence/Entity Graph, Hunter, отчётность, Capability Management и очередность реализации.
- [План продуктовой проверки Runner Controller](roadmap/RUNNER_CONTROLLER_PRODUCT_VALIDATION_PLAN_2026-08-30.md) — ограниченный provider-free срез `contract → inventory → runtime identity` на существующем Site Auditor burst acceptance.
- [SEF T0 baseline](baseline/SEF-T0-BASELINE-2026-07-28.md) — точные SHA сервиса, stage, Sentinel и инфраструктуры, evidence ATS-09A, teardown/no-orphan и честная фиксация красного внешнего валидатора.
- [SEF Benchmark-20 v0.1](benchmarks/SEF-BENCHMARK-20-V0.1.md) — замороженная выборка из 20 компаний и ручной identity-эталон для первых пяти.
- [Identity Benchmark-5 v0.1](benchmarks/IDENTITY-BENCHMARK-5-V0.1.md) — воспроизводимый HTML → signals → Entity Resolution regression по Golden-5.
- [SEF Mission и Evidence Contract v0.1](architecture/SEF-CONTRACT-V0.1.md) — provider-neutral модель данных, SQL-миграция и fail-closed инварианты доказательности.
- [SEF Provider Gateway v0.1 → SR-G1](architecture/SEF-PROVIDER-GATEWAY-V0.1.md) — единый контракт Yandex/SearXNG/Tavily, operational readiness, fail-closed pricing/budget/quota, fallback и безопасная телеметрия.
- [SEF Document Fetch/Extract v0.1 → SR-G1](architecture/SEF-DOCUMENT-FETCH-EXTRACT-V0.1.md) — HTTP-first загрузка, каскад кодировок, multi-area/header/footer extraction, redirect/canonical identity, digest, locators и строгий переход `hint → document → evidence`.
- [Mission Orchestrator v0.1](architecture/MISSION-ORCHESTRATOR-V0.1.md) — единый Mission Contract для UI/REST/MCP, типизированный `NextActionPlan`, детерминированный Policy Guard, lifecycle и trace оборотов.
- [Evidence Crawler Bootstrap v0.1](architecture/EVIDENCE-CRAWLER-BOOTSTRAP-V0.1.md) — исполнимый bootstrap-оборот `#85`: robots/sitemap, bounded same-domain fetch, identity signals, document hints и outcome для следующего плана.
- [Provisional Entity Resolution v0.1](architecture/ENTITY-RESOLUTION-PROVISIONAL-V0.1.md) — candidate-срез `#84`: проверка provenance, competing candidates, explicit conflicts/unresolved, история ревизий и targeted search pivots без повышения bootstrap hints до accepted identity.
- [Identity Search & Evidence Guard v0.1](architecture/IDENTITY-EVIDENCE-GUARD-V0.1.md) — Tavily discovery, отдельный fetch первичного документа, fail-closed promotion в evidence-backed `EntityIdentifier` и открытие targeted crawl без ложного `resolved`.
- [Incident: live Entity Resolution attribution](incidents/2026-07-30-entity-resolution-live-attribution.md) — real-world conflict на Selectel/Sendy/БСК, причина и граница candidate-исправления `0.16.1`.
- [SEF Claim & Evidence Ledger v0.1](architecture/SEF-CLAIM-EVIDENCE-LEDGER-V0.1.md) — evidence tiers, freshness, conflict groups, effective review и fail-closed client eligibility.
- [SEF Company Profile v1](architecture/SEF-COMPANY-PROFILE-V0.1.md) — 14 секций, шесть критических пробелов и evidence-only проекция Ledger.

## Политики управления

- [Политика и правила управления проектом](governance/AIMETON_Project_Management_Policy.md) — GitHub Projects как оперативный центр, источники истины, иерархия Initiative → Epic → Issue → Sub-issue, обязательные поля и представления, шаблоны Issue/PR, Definition of Ready/Done и трассировка Alpha → Omega.

## Архитектура памяти и RAG

- [Детальный план реализации Evidence Memory](roadmap/AIMETON_Site_Auditor_Evidence_Memory_implementation_plan.md) — подсистемный трек главного плана: этапы M0–M10, простые действия, критерии приёмки, контрольные точки и первый спринт EM-01.

## Очерёдность старта

1. Создать GitHub Project `AIMETON Development Control` по принятой политике.
2. `SA-01` — стабилизация текущего поискового и MCP-контура.
3. Evidence и Entity contracts.
4. Федеративный SearchProvider layer.
5. `EM-01` — сохранение оригиналов и provenance за feature flag.
6. Новый отчёт и дальнейшее развёртывание Раструба.

## Принцип реализации

```text
малое изменение
→ тест
→ наблюдаемый результат
→ фиксация опыта
→ следующий простой шаг
```

Индексы RAG не являются источником истины: оригиналы и provenance должны позволять полностью перестроить поисковый слой.

- [Opt-in Deep Research v0.1](architecture/AUDIT-DEEP-RESEARCH-V0.1.md) — явное согласие на расход LLM, полная обработка, учёт и остановка.

- [Hunter Continuous Search v0.1](architecture/HUNTER-CONTINUOUS-SEARCH-V0.1.md) — длительный поиск без общего тайм-аута, наблюдаемый прогресс и явная пользовательская остановка с уже завершёнными результатами.

- [Document Preflight v0.1](architecture/DOCUMENT-PREFLIGHT-V0.1.md) — предварительный отбор больших документов перед полной LLM-обработкой.

- [Hunter HTTP 500 — 2026-09-13](incidents/HUNTER-HTTP-500-2026-09-13.md) — воспроизведение и корректирующий кандидат для поиска клиентов.

- [Настройки исследования: серверная основа](architecture/USER_RESEARCH_SETTINGS_V0.1.md) — #915, draft P0-B: schema/revisions/snapshots/API; UI и enforcement ещё не подключены.

- [User research execution v0.1](architecture/USER_RESEARCH_EXECUTION_V0.1.md) — applied time settings, preflight gates and lifecycle boundaries.
