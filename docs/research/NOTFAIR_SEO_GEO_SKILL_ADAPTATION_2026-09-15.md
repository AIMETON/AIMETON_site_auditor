# NotFair → AIMETON Site Auditor: исследовательская фиксация SEO/GEO skill-паттернов

**Дата:** 2026-09-15  
**Статус:** `STUDY / ADAPT PATTERNS / NO FORK`  
**Tracker:** #920  
**Источник:** https://github.com/nowork-studio/notfair-plugin  
**Лицензия upstream:** MIT

## 1. Зачем это AIMETON

NotFair полезен не как готовый runtime и не как новая зависимость ядра, а как практический reference того, как разложить маркетинговую экспертизу на узкие процедурные skills и связать их с измеримым результатом.

Для Site Auditor это даёт прямой продуктовый мост:

```text
доказательный crawl / business intelligence
→ SEO / GEO / competitor / conversion diagnostics
→ приоритизированные исправления
→ измеримый пилот
→ повторный аудит / drift
```

Это ближе к продаваемому результату для малого и среднего бизнеса, чем общий «AI-анализ сайта» без измеримой поверхности улучшения.

## 2. Что подтверждено в upstream

`AGENTS.md` выступает универсальным intent resolver: пользовательский запрос сопоставляется со специализированным `SKILL.md`, после чего агент выполняет процедуру skill. SEO, paid-ads, Google Ads, Meta Ads, analytics и другие skills объявлены host-agnostic.

Для AIMETON наиболее релевантны:

- `seo-analysis` — full-site SEO, GSC, technical crawl, metadata/schema, PageSpeed/Core Web Vitals, actionable plan;
- `geo-optimizer` — GEO/AEO для AI search, evidence/authority/structure/crawlability;
- `competitor-pages` — gap analysis страницы против конкурентов;
- `sxo` — search experience / conversion diagnostics;
- `schema-markup-generator` — structured data;
- `seo-drift` — baseline/compare/regression monitoring;
- `local-seo`, `keyword-research` — дальнейшее расширение;
- `search-console`, `google-analytics` — connected measurement;
- paid ads skills — только поздний read-only/controlled-action слой.

Upstream live-интеграции Google Ads, Meta Ads, X Ads, LinkedIn Ads, Search Console, Google Analytics и GoHighLevel используют единый hosted NotFair MCP с OAuth. Это удобный продуктовый shortcut для NotFair, но не приемлемая обязательная зависимость AIMETON core.

## 3. Главный переносимый паттерн

```text
Intent
→ Specialist Skill
→ Evidence / Data Requirements
→ Provider-neutral capability request
→ Deterministic checks
→ Structured findings
→ Quality gate
→ Measurable next action
→ Re-measure / drift
```

Skill должен описывать **что требуется получить и проверить**, но не владеть конкретным провайдером. Конкретный API/MCP/browser/search backend выбирается AIMETON Capability Registry / Provider Router.

Это предотвращает жёсткую привязку процедуры к NotFair MCP, Google, одному LLM или одному agent host.

## 4. Зёрна из ключевых skills

### 4.1 `seo-analysis`

Полезные идеи:

- cold-site audit должен работать даже без подключённого GSC;
- подключённые first-party данные повышают уверенность, но не являются обязательными для базового аудита;
- история предыдущих аудитов позволяет отличать `resolved / improved / still present / worsened`;
- итог должен быть не каталогом сотен замечаний, а коротким списком изменений с максимальным ожидаемым эффектом;
- URL, property/account и business context должны разрешаться до тяжёлого анализа.

Адаптация AIMETON: использовать уже собранный corpus/evidence run, не создавать второй crawler.

### 4.2 `geo-optimizer`

Upstream использует отдельный GEO Score 0–100 с четырьмя группами сигналов:

- evidence density;
- structure / position;
- authority signals;
- AI crawlability.

Самая ценная часть — жёсткий anti-fabrication gate: статистика, цитата или эксперт без проверяемого источника не должны становиться рекомендацией/контентом.

Для AIMETON это должно быть усилено существующим Claim/Evidence Ledger: GEO finding ссылается на документы/claims либо помечается как hypothesis.

Дополнительно полезны veto/cap checks: конфликтующие данные, title/content mismatch, отсутствие first-party identity, блокировка AI crawlers, неподтверждённые YMYL authority claims.

### 4.3 `competitor-pages`

Переносимый алгоритм:

```text
query / business intent
→ own page
→ 1..N competitor pages
→ common extraction schema
→ intent match
→ coverage matrix
→ depth / media / freshness
→ E-E-A-T / schema / links
→ concrete gap brief
```

Для Site Auditor это естественно объединяется с уже существующими Entity/Evidence моделями и OSINT competitor plane.

### 4.4 Drift / repeatability

Повторный аудит должен хранить baseline и diff, а не просто генерировать новый PDF. Это создаёт подписочный продукт: «что реально изменилось после рекомендаций».

## 5. Goal-loop как архитектурный reference

Отдельный NotFair runtime реализует полезную дисциплину:

```text
goal
→ server/mechanically verified metric
→ measured baseline
→ one bounded action
→ falsifiable predicted effect
→ observation window
→ re-measure
→ compare prediction vs reality
→ next action
```

Сильное правило: агент не сам объявляет значение метрики, по которой оценивается. Метрика должна пересчитываться независимым/детерминированным контуром.

Для AIMETON это не новый controller. Паттерн должен встраиваться в существующие Mission Kernel / Execution Fabric / Evidence / Verification / Recovery.

## 6. Что НЕ переносить

1. **Не форкать весь NotFair сейчас.** Поддержка форка не окупается: ценность сосредоточена в ограниченном наборе процедур и архитектурных паттернов.
2. **Не использовать hosted NotFair MCP как core dependency.** Только reference или временный optional adapter, если когда-либо понадобится.
3. **Не копировать NotFair runtime как controller.** У AIMETON уже есть собственный mission/execution контур.
4. **Не принимать unsandboxed harness model как норму.** Upstream Codex может запускаться с bypass approvals/sandbox; это не соответствует AIMETON policy boundary.
5. **Не повторять хранение OAuth tokens без application-layer encryption как целевую модель.**
6. **Не разрешать Ads mutations по умолчанию.** Сначала read-only measurement; любые траты/изменения требуют approval, budget/spend envelope, audit log и наблюдения результата.

## 7. AIMETON Skill Contract — исследовательский кандидат

Минимальная форма:

```yaml
skill:
  id: seo.geo.audit
  version: 0.1
  intents: []
  required_capabilities: []
  optional_capabilities: []
  inputs: []
  evidence_requirements: []
  deterministic_checks: []
  scoring_contract: null
  output_schema: null
  quality_gates: []
  mutation_scope: none | proposed | approved
  budget_class: free | bounded | explicit_approval
  observation_contract: null
  provenance:
    method_sources: []
    adapted_from: []
```

Skill Contract не должен содержать секреты, provider-specific OAuth state или обязательный namespace конкретного MCP.

## 8. Рекомендуемый первый набор

P0 read-only:

1. SEO technical/on-page audit;
2. GEO/AEO audit;
3. competitor page gap;
4. SXO/conversion gap;
5. schema diagnostics/generation proposal;
6. SEO/GEO drift baseline.

P1 connected read-only:

7. GSC;
8. GA4;
9. local/keyword research.

P2 controlled action:

10. CMS/content changes через PR/diff/review;
11. Ads analysis;
12. Ads mutation только через отдельный policy gate.

## 9. Решение

`ADAPT, DO NOT IMPORT AS A SUBSYSTEM`.

NotFair остаётся внешним reference. В Site Auditor переносим методику и контракты, но выполнение строится на собственных AIMETON corpus/evidence/providers. Реализация должна следовать после текущего quality path, потому что SEO/GEO score поверх неполного crawl создаст ложную точность.

## 10. Связанные материалы

- `docs/roadmap/AUDIT_MISSION_QUALITY_PLAN_2026-09-15.md`;
- `docs/roadmap/SEO_GEO_INTELLIGENCE_PLAN_2026-09-15.md`;
- `docs/roadmap/AIMETON_Site_Auditor_full_system_development_plan.md`;
- `docs/architecture/SEF-CLAIM-EVIDENCE-LEDGER-V0.1.md`;
- `docs/architecture/SEF-PROVIDER-GATEWAY-V0.1.md`;
- `docs/architecture/MISSION-ORCHESTRATOR-V0.1.md`;
- GitHub Issue #920.