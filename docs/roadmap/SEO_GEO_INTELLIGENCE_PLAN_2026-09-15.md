# AIMETON Site Auditor — SEO/GEO Intelligence Plan

**Дата:** 2026-09-15  
**Статус:** `PLANNED / DEPENDS ON CURRENT AUDIT QUALITY PATH`  
**Tracker:** #920  
**Research:** `../research/NOTFAIR_SEO_GEO_SKILL_ADAPTATION_2026-09-15.md`

## 1. Сверхзадача

Усилить путь Site Auditor от доказательного исследования компании к быстрому продаваемому результату:

```text
полный и проверяемый audit corpus
→ SEO/GEO/competitor/conversion diagnosis
→ 3–7 наиболее ценных исправлений
→ измеримый пилот
→ повторное измерение
→ доказанный эффект / следующий шаг
```

Этот трек **не заменяет** текущую миссию восстановления полноты crawl/evidence и управляемого исполнения. SEO/GEO поверх неполного корпуса создаёт ложную точность, поэтому продуктовая реализация зависит от достаточной зрелости `AUDIT_MISSION_QUALITY_PLAN_2026-09-15.md`.

## 2. Архитектурная граница

```text
Mission / Intent
  ↓
AIMETON Skill Resolver
  ↓
Skill Contract
  ↓
Capability Resolver / Provider Router
  ↓
existing crawl + evidence + connected data
  ↓
Deterministic checks / scoring
  ↓
Findings + Evidence refs + hypotheses
  ↓
Commercial recommendation
  ↓
Observation contract / repeat run
```

Не создавать второй crawler, второй provider controller или второй mission runtime.

## 3. Первая продуктовая матрица skills

| Skill AIMETON | Источник паттерна | MVP | Данные | Mutation |
|---|---|---:|---|---|
| `seo.site.audit` | NotFair `seo-analysis` | SG-1 | own corpus + optional public metrics | none |
| `geo.site.audit` | `geo-optimizer` | SG-1 | own corpus + public web evidence | none |
| `seo.competitor.gap` | `competitor-pages` | SG-1 | own + competitor documents | none |
| `sxo.gap.audit` | `sxo` | SG-1 | page + funnel hints | none |
| `schema.audit` | `schema-markup-generator` | SG-1 | HTML/JSON-LD | proposal only |
| `seo.drift` | `seo-drift` | SG-2 | versioned runs | none |
| `search.gsc.measure` | `search-console` | SG-2 | user-authorized GSC | read-only |
| `analytics.ga4.measure` | `google-analytics` | SG-2 | user-authorized GA4 | read-only |
| `ads.audit` | paid ads skills | SG-4 | user-authorized ads | read-only first |
| `ads.optimize` | paid ads skills | SG-4+ | ads + policy | explicit approval only |

## 4. Скоринг

SEO/GEO нельзя смешивать с существующими business/evidence scores.

Минимум отдельных показателей:

- `seo_technical_score`;
- `seo_content_fit_score`;
- `geo_score`;
- `geo_evidence_density`;
- `geo_structure_score`;
- `geo_authority_score`;
- `geo_ai_crawlability_score`;
- `competitor_gap_score`;
- `conversion_gap_score`;
- `measurement_confidence`.

Каждый score хранит:

```yaml
value: 0..100
method_version: ...
inputs_digest: ...
evidence_refs: []
unknowns: []
vetoes: []
computed_at: ...
```

Если входные данные недостаточны, score должен быть `unknown/partial`, а не искусственно нормализованным числом.

## 5. Quality gates

Обязательные правила:

1. fabricated statistics/quotes/entities запрещены;
2. любой внешний факт имеет URL/document/evidence provenance;
3. hypothesis визуально и машинно отделена от evidence-backed finding;
4. missing GSC/GA4 не ломает cold-site audit;
5. отсутствие данных не превращается в негативный score без явного контракта;
6. recommendations сортируются минимум по `impact × confidence × effort`, но численные веса версионируются;
7. score считается воспроизводимо на фиксированном input digest;
8. повторный run показывает delta и method-version changes;
9. mutation никогда не является скрытым продолжением read-only skill.

## 6. Этапы

### SG-0 — contracts and fixtures

**Безопасен параллельно с текущим quality path.**

- [ ] AIMETON Skill Contract v0.1;
- [ ] intent routing table;
- [ ] шесть read-only skill contracts;
- [ ] mapping skill → required capabilities → existing providers;
- [ ] offline HTML/JSON-LD fixtures;
- [ ] score schemas и veto semantics;
- [ ] anti-fabrication/evidence tests;
- [ ] методическая attribution upstream MIT.

**Gate:** контракты не требуют NotFair runtime/MCP и используют существующие AIMETON evidence IDs.

### SG-1 — cold-site SEO/GEO MVP

**Зависит от достаточного P1 crawl/evidence качества.**

- [ ] reuse existing InvestigationRun/corpus;
- [ ] technical/on-page SEO diagnostics;
- [ ] GEO/AEO pillars;
- [ ] competitor gap matrix;
- [ ] schema findings/proposals;
- [ ] SXO gap;
- [ ] top recommendations с evidence refs;
- [ ] report section/API payload.

**Gate:** один audit run выдаёт воспроизводимый SEO/GEO block без GSC/GA4 и без второго обхода сайта.

### SG-2 — measurement and drift

- [ ] run baseline;
- [ ] comparable follow-up run;
- [ ] method-version-aware delta;
- [ ] GSC adapter/read contract;
- [ ] GA4 adapter/read contract;
- [ ] observation windows;
- [ ] resolved/improved/unchanged/worsened statuses.

**Gate:** система различает изменение сайта, изменение внешней метрики и изменение метода расчёта.

### SG-3 — commercial surface

- [ ] экспресс-блок в AI-аудите;
- [ ] 3–7 top fixes;
- [ ] оценка effort/impact/confidence;
- [ ] pilot proposal;
- [ ] success metric + baseline + observation window;
- [ ] повторный аудит как продуктовая подписка/retainer.

**Gate:** рекомендация переводится в конкретный пилот с проверяемым критерием успеха.

### SG-4 — paid media

- [ ] read-only platform audits;
- [ ] separate authorization scope;
- [ ] spend/cost visibility;
- [ ] proposal mode;
- [ ] approval gate;
- [ ] one bounded mutation;
- [ ] predicted effect;
- [ ] observation window;
- [ ] rollback/review path;
- [ ] audit log.

**Gate:** без approval/budget policy никакая write capability не вызывается.

## 7. Связь с текущим планом качества

Порядок зависимости:

```text
P0/P1 audit quality
→ stable corpus/evidence refs
→ SG-0 contracts/fixtures
→ SG-1 cold-site SEO/GEO
→ P2 workspace/report integration
→ SG-2 measurement/drift
→ SG-3 commercial pilot loop
→ SG-4 controlled paid media
```

SG-0 разрешён заранее, потому что не изменяет production runtime. SG-1 не должен считаться готовым, пока качество first-party crawl не доказано на regression/live acceptance.

## 8. Goal-loop, адаптированный к AIMETON

Использовать только как дисциплину измерения:

```text
business goal
→ independently measured metric
→ baseline
→ proposed bounded action
→ predicted effect
→ observation window
→ re-measure
→ prediction error / learned evidence
→ next safe action
```

Значение метрики не должно поступать из self-report самого агента. Для SEO/GEO часть метрик может быть deterministic/offline, для GSC/GA4/Ads — через авторизованный provider adapter.

## 9. Definition of Done для первого коммерчески полезного среза

SG-1 + минимальный SG-3 готовы только если:

- cold URL даёт SEO/GEO блок без подключений аккаунтов;
- findings имеют evidence/hypothesis status;
- top fixes конкретны и локализованы по URL/section;
- score versioned/reproducible;
- competitor gap поддерживает минимум own page + 1 competitor;
- JSON-LD анализ безопасно обрабатывает malformed input;
- AI crawler policy проверяется отдельно от общего robots verdict;
- результаты доступны в UI/API/report;
- один и тот же corpus повторно не скачивается ради каждого skill;
- фиксированный regression набор проходит CI;
- live audit на разрешённом сайте подтверждает полезность рекомендаций.

## 10. Решение по NotFair

- **Fork:** нет на текущем этапе;
- **Runtime adoption:** нет;
- **Hosted MCP as core:** нет;
- **Skill methodology:** адаптировать;
- **Goal-loop measurement discipline:** адаптировать в существующий AIMETON mission/execution fabric;
- **License attribution:** сохранить MIT/source references;
- **Re-evaluate fork:** только если фактическая доля повторно используемого upstream кода/updates станет выше стоимости независимой адаптации.
