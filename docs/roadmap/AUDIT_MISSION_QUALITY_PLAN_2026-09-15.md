# План восстановления качества и управляемого исполнения аудита

Checkpoint: 2026-09-15T02:39:46Z
Base: `5f9d8ca2415f8b7f57a3a848cb6bc3427ce0d769`.
Статус: план принят к исполнению по прямому поручению владельца в чате 2026-09-15; реализация и live-приёмка учитываются отдельно.
Область: кнопки «AI-аудит сайта» и «Исследование компании», их общий evidence/mission runtime. Hunter — только совместимые общие компоненты и handoff.

## 1. Миссия и целевая функция

Сверхзадача: доказательная бизнес-разведка, приводящая к обоснованному предложению, контакту, платному пилоту, качественной поставке и повторной продаже.
Миссия: восстановить проверяемый профиль нужной компании и объяснимые AI-возможности, сохраняя управление временем и расходами за пользователем.

Приоритеты лексикографические, а не произвольная сумма баллов:
1. Соблюдать identity, provenance, доступ, пользовательские ограничения и release gates.
2. Максимизировать полноту релевантных подтверждённых фактов и закрытие критических пробелов миссии.
3. Улучшать проверяемость коммерческой гипотезы и пригодность следующего действия.
4. При сопоставимом качестве снижать дублирование, задержку и стоимость.

Коммерческий score, полнота профиля, доказательность, покрытие входа и готовность выпуска — разные показатели. Один документ не означает «компания исследована».
Исчерпание очереди известных URL не означает исчерпывающий поиск Интернета.

## 2. Основания и границы выводов

- [Главный план](AIMETON_Site_Auditor_full_system_development_plan.md).
- [Document pipeline](../architecture/SEF-DOCUMENT-FETCH-EXTRACT-V0.1.md).
- [Bootstrap crawler](../architecture/EVIDENCE-CRAWLER-BOOTSTRAP-V0.1.md).
- [Profile loop](../architecture/AUDIT-RESEARCH-PROFILE-LOOP-V0.1.md).
- [Deep research](../architecture/AUDIT-DEEP-RESEARCH-V0.1.md).
- [Business workspace](../BUSINESS_AUDIT_UI_GAPS.md).
- Issues #173, #422, #454, #456, #797, #850; изменения #906–#913.
- Код: scraper, document_pipeline, verified_analysis, external_verification, company_intelligence_runtime, analysis_async_api и соответствующие UI.

Подтверждено чтением base: селективные HTML-теги пропускают часть текста; обычный URL-аудит не использует полноценный bootstrap до внешнего поиска; рекурсивный обход в external_verification включается только в deep; company-intelligence синхронен; terminal semantics UI/backend расходятся.
Запрет dynamic fallback при allowed_hosts — намеренный security boundary bootstrap, а не разрешение удалить проверку.
Прежняя отсечка scraper на 45000 символах уже снята: её нельзя повторно заявлять текущим дефектом.
Исторические Selectel/IMLS-прогоны — основания regression cases, не доказательство состояния сегодняшнего deployment.
Причина конкретного пользовательского пропуска остаётся provisional до восстановления trace/корпуса того запуска.

## 3. Пользовательские настройки времени и затрат — обязательный контракт

Отдельная страница/панель «Настройки исследования», сохранённая на сервере для пользователя. Общие defaults, пресеты сайта/компании и явные overrides одного запуска. UI показывает effective значения до старта.

| Параметр | Семантика |
|---|---|
| mission_timeout_seconds | Общее время; null = без пользовательского общего дедлайна |
| request_timeout_seconds | Тайм-аут одной сетевой попытки; не завершает миссию целиком |
| llm_call_timeout_seconds | Тайм-аут LLM-вызова; сохранение успешных порций и повтор по политике |
| progress_warning_seconds | Через сколько сообщить о длительной операции; не прерывает работу |
| cost_warning_amount | Денежный порог предупреждения, валюта обязательна |
| cost_limit_amount | Максимальная стоимость миссии, null = явный режим без пользовательского денежного потолка |
| token_warning / token_limit | Отдельные пороги токенов; не выдавать токены за денежную оценку |
| threshold_action | Для мягкого порога: notify_continue или pause; hard limit всегда запрещает новые расходы сверх резерва |
| retry_count / retry_backoff | Повторы в пределах оставшегося времени и зарезервированного бюджета |
| unknown_price_action | При неизвестной цене: pause или явное согласие на работу без гарантированного денежного потолка |

- Нулевые, отрицательные, NaN и бесконечные числа отклонять; null не путать с отсутствующим полем. Проверять warning <= hard limit.
- Порог времени и расходов настраивает пользователь; ENV не должен скрыто подменять принятый per-run snapshot.
- Инфраструктурные пределы отдельного вызова/контекста, квоты и ограничения доступа сохраняются; UI/API показывают requested/effective и причину ограничения. Они не должны молча объявлять всю миссию завершённой.
- Сохранение настроек не изменяет уже запущенную миссию. Изменение активного бюджета/времени — отдельная авторизованная ревизия с UTC и audit record.
- Snapshot: owner, service preset, settings revision, effective configuration, timestamp, digest; привязан к mission_id/run_id.
- Учёт: резерв до каждого платного вызова, атомарная проверка при конкуренции, settlement по фактическому usage, освобождение остатка. Считать retries и fallback.
- Неизвестная цена — unknown/not_reported, никогда 0. Цена/валюта/источник тарифа/версия фиксируются; конвертация требует известного курса и времени.
- При жёстком потолке сумма settled + reserved не превышает limit. Если нельзя ограничить стоимость отдельного запроса, не заявлять гарантированный hard cap.
- Использовать существующие auth/settings/research_control/SearchGateway/usage механизмы; не создавать второй provider controller.
- Миграция deep checkbox: временно совместимый adapter; отделить качество обхода от согласия на неограниченные затраты. Изменение defaults не включает платные возможности без согласия.
- Owner isolation, CSRF, раздельные users, REST/MCP parity и race tests обязательны.

## 4. Целевая схема исполнения

Обе кнопки создают принадлежащую пользователю Mission через существующий orchestrator.
URL-first: загрузка → first-party crawl → identity → external gap search.
Entity-first: кандидаты идентичности → подтверждение компании/сайта → тот же crawl и evidence pipeline.
Общий путь: настройки snapshot → persistent run → corpus/documents → evidence/claims → profile revision → commercial hypotheses → review/export.

Рабочие состояния: queued, running, waiting_provider, pause_requested, paused, stop_requested.
Терминальные: completed, stopped, failed; качество отдельно full/partial/degraded и completion_reason.
Достигнут лимит — checkpoint и paused либо stopped по явно выбранной политике; готовность выпуска отдельно.
Переходы атомарны; terminal не возвращается в running; один submit/idempotency key создаёт один run.
После разрыва браузера сервер продолжает; после restart — восстановление lease/checkpoint без дублирования оплаченных вызовов. Если ответ провайдера потерян, фиксировать uncertain outcome, а не автоматически повторять оплату.
Статус пользовательской миссии не должен зависеть от одного worker процесса.

## 5. Этапы и отдельные reviewable PR

| Этап | Изменения | Зависимости | Проверяемая приёмка |
|---|---|---|---|
| P0-A | Потери HTML: полный содержательный текст, direct div/span/address, таблицы, длинные блоки, общая логика двух извлекателей | Base | Контрольные поверхностные факты извлекаются обоими путями, locators стабильны, нет исполнения script |
| P0-B | Настройки пользователя: versioned schema/store/API/UI, overrides, snapshot; тайм-ауты/пороги/действия | P0-A не блокирует разработку | Два пользователя независимы; введённые значения доходят до runtime; reload сохраняет настройки |
| P0-C | Применение policy: request/LLM/mission deadlines, cost reservations/settlement, warnings, pause/stop | P0-B | Fake clock/provider: ни одного нового вызова после hard limit, все успешные результаты сохранены, unknown price честно блокирует обещание cap |
| P0-D | Единый persistent run, lease/recovery, async company API, owned mission linkage, один UI submit | P0-B/C | UI/REST/MCP одинаковы; restart/poll/stop/retry не создают второй запуск и не теряют результат |
| P1-A | Общий first-party bootstrap для обеих кнопок; menu/footer/sitemap/robots, приоритет контактов/реквизитов/команды/продуктов | P0-A/D | Найдены ссылки без поисковика и без deep; недоступный поиск не мешает обходу; URL за пределами policy не запрашивается |
| P1-B | Browser fallback с проверкой каждого redirect/subrequest и egress; оценка body completeness; SSR HTML не терять из-за Next marker | P1-A | SSR/SPA fixtures, private redirect и cross-domain negative tests, реальный browser smoke в разрешённом контуре |
| P1-C | JSON-LD как отдельные first-party declarations; PDF/DOCX адаптеры, типизированная неподдерживаемость | P0-A, existing identity/document contracts | Malformed/nested JSON-LD безопасен; declarations не становятся accepted identity автоматически; таблицы и документы имеют provenance |
| P1-D | Полнота извлечения: все блоки → chunks → facts; ошибки reasoning не удаляют извлечённое; entity conflict/gap-driven follow-up | P1-A/C, P0-C | Поздние факты сохранены, другой субъект отклонён, незавершённые chunks явно перечислены |
| P2-A | Единый workspace, ссылки ID→URL→quote, source status/freshness, история, диалог, revision delta, экспорт | P0-D/P1-D | Пользователь проверяет каждый вывод; весь corpus доступен с пагинацией; нет потери фактов в кратком sales view |
| P2-B | Коммерческая целевая функция: проблема→основание→решение→пилот→метрика успеха→следующее действие | P2-A | Гипотезы отделены от фактов; no-data не порождает уверенную рекомендацию; human review/report release сохраняются |
| P3 | Замороженные real-company dossiers, сравнительная live приёмка обеих кнопок, обновление docs/status | Все | Точная версия deployment, факты/стоимость/stop/recovery доказаны; регрессии разобраны |

Порядок первой поставки: сначала опубликовать этот план; затем P0-A как маленький проверяемый инкремент. Пользовательские настройки — следующий обязательный срез, а не косметическая опция в конце.
Слияние и deployment учитывать отдельно от готовности кода. Текущая инструкция разрешает план и начало реализации; прежние разрешения на merge конкретных PR не переносятся на новые.

## 6. Метрики и тестовый набор

Единица оценки — контрольный факт с URL, точной цитатой, locator, сущностью и периодом.
Воронка потерь: discovered URL → fetched document → extracted block → identity verified → processed chunk → retained fact → visible/exported fact.
Каждый пропуск имеет этап и reason_code.

Fixtures: div/span/address; таблица; JSON-LD-only; SSR Next; SPA menu shell; поздний блок >45000 chars; длинный блок >20000; sitemap-only; www redirect; PDF; одноимённая компания; robots deny; provider timeout; unknown price; concurrent cost; restart; browser disconnect.
Для поддерживаемых HTML-fixtures P0-A: 100% заранее размеченных доступных текстовых фактов в обоих извлекателях, отсутствие script/comment мусора и сохранение quote promotion.
Остальные этапы: все размеченные критические доступные факты контрольного набора либо извлечены, либо имеют точную объяснимую причину пропуска; неподдерживаемость считается пробелом, не успехом.
Внешние real-company эталоны: Selectel и IMLS из #850/#797 плюс клиника, промышленная компания, малый бизнес. Заморозить lawful HTML/document snapshots и manual ground truth до сравнения.
Метрики: critical fact recall, entity precision, evidence traceability, late-fact recall, failed/unsupported documents, duplicate paid calls, cost per verified fact, time to first evidence, stop acknowledgement и время завершения in-flight.
Число sources не считать числом независимых документов. CI green и HTTP 200 не равны качеству.

## 7. Риски и контрмеры

- Расширение extraction увеличит текст: структурные блоки, отсутствие повторного захвата родителей, чанки без потери хвоста; измерять дублирование.
- Recursion/crawl explosion: canonical URL dedupe, приоритизация, пользовательская policy, checkpoint очереди, никаких скрытых отсечек.
- SSRF/domain fallback: не снимать защиту до переноса эквивалентных проверок на dynamic path.
- Платные повторы после restart: persistent call identity/reservation/uncertain state.
- Claim contamination: first-party declaration и пользовательское утверждение не равны независимо подтверждённому факту.
- Миграция API: additive fields и adapter, contract tests существующих UI/MCP/экспорта.
- Изменения общего инфраструктурного egress/tariff или Sentinel приёмки потребуют связанных задач в соответствующих canonical repo; данный план их не объявляет реализованными.

## 8. Журнал исполнения и передача

- 2026-09-15: план подготовлен на указанном base. P0-A — первый исполняемый срез; P0-B/C обязательны следующими. Все live-gates открыты.
- В каждом PR обновлять этот журнал, CURRENT_STATUS.md и профильный контракт; docs/README.md содержит ссылку на план.
- Не менять существующие даты GitHub Project. Задача исполнения ссылается на #173, #422, #456, #850 и этот документ.
- Done всей миссии: обе кнопки исполняют common policy/run/evidence contract, пользователь управляет временем и затратами, контрольные факты и реальные прогоны приняты. Первый PR не закрывает всю миссию.

### P0-A implementation candidate — 2026-09-15T02:39:46Z

Tracking: #915. Shared uncovered DOM text traversal now feeds scraper and document pipeline. Direct container/inline/address text is retained without re-extracting covered parent subtrees. Oversized blocks are split with stable part locators; equal chunks at distinct offsets survive.

Six new cases fail on base SHA and pass on candidate. Full suite before the final equal-chunk regression addition: 1404 passed, 1 xfailed, 6 subtests. Final focused extraction/document suite: 25 passed. App import and diff check verified separately. CI and deployed real-company acceptance remain pending. JSON-LD, guarded dynamic rendering, traversal integration and user settings are not implemented in this slice.

Next reviewable slice: P0-B user settings store/API/UI and per-run snapshot, followed by P0-C end-to-end enforcement. No aggregate budget change is activated by P0-A.

### Continuation — 2026-09-15T03:33:54.995917+00:00

Owner directed “Проверяй и действуй”. #916 merged with all PR gates green; merge e4941e5c052842bd9600f941a936160fd44e71bb, post-merge Baseline 34925159315 successful; stage health independently confirms exact SHA. Frozen real-company quality acceptance remains open.

P0-B foundation draft: schema, owner/service revision persistence, conflict-safe writes, immutable internal snapshots, authenticated/CSRF router tested in isolation (16 tests). Not mounted; no UI or execution enforcement yet. Next: connect snapshot to owned launch, implement P0-C reservations/time guards, then publish user settings UI. See USER_RESEARCH_SETTINGS_V0.1.md. This is progress, not completion of P0-B/C or whole mission.

2026-09-15T07:56:45.410907+00:00: P0-C draft adds atomic durable reservations, single-dispatch claim, settlement, unknown-price guards and sticky warning/hard-limit states. Missing LLM usage hooks repaired. 1453 local tests pass, 1 xfail, 6 subtests. This is admission foundation; runtime quote authority, deadlines and UI integration remain gates in #917.
