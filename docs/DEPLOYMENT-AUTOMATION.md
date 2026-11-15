# Stage deployment automation

## Назначение

Контур разворачивает точный commit из `main` на VPS stage после успешного завершения workflow `Baseline CI`.

```text
GitHub main + green Baseline CI
  → self-hosted runner on aimeton-main-server
  → exact checkout by full commit SHA
  → isolated build of the candidate Docker image
  → protected runtime-secret materialization
  → transactional app-source switch
  → docker compose up from the candidate image
  → container health
  → external health and MCP smoke
  → evidence artifact
```

OpenStack API immers.cloud в этот процесс не входит. Он запланирован отдельным внешним инфраструктурным контуром в SA-02.2. Команды внутри Ubuntu выполняет self-hosted GitHub Runner.

## Файлы

- `.github/workflows/deploy-stage.yml` — оркестрация после зелёного CI и ручной аварийный запуск;
- `scripts/deploy_stage.sh` — транзакционное развёртывание, smoke и rollback;
- `/opt/aimeton/auditor-stack` — постоянный deployment stack на VPS;
- `/opt/aimeton/auditor-stack/app-source-sha.txt` — фактически развёрнутый SHA;
- `/opt/aimeton/auditor-stack/.deployments/` — резервные и неуспешные bundle.
- `/opt/aimeton/auditor-stack/.runtime-secrets.env` — runtime secrets с
  правами `0600`, не входящие в source bundle;
- `/opt/aimeton/auditor-stack/docker-compose.runtime-secrets.yml` —
  несекретный Compose override, подключающий runtime secret file и
  provider-policy.

Repository/environment secret `TAVILY_TOKEN` обязателен для deployment
версии `0.16.0+`. Workflow проверяет только наличие; значение не печатается,
не попадает в source bundle и deployment artifacts.

## Предусловия runner

Runner должен работать под пользователем, который имеет:

- чтение и запись в `/opt/aimeton/auditor-stack`;
- доступ к Docker socket;
- команды `docker`, `curl`, `python3`, `tar`, `flock`;
- исходящий HTTPS-доступ к GitHub и `stage-auditor.aimeton.ru`.

На сервере уже установлен runner в `/home/ubuntu/actions-runner`.

## Автоматический запуск

Workflow `Deploy Stage` получает событие `workflow_run` только для `Baseline CI`. Deployment выполняется, если одновременно:

1. CI завершился с `success`;
2. проверялся `main`;
3. доступен полный `head_sha`.

Checkout выполняется именно по этому SHA, после чего `git rev-parse HEAD` сравнивается с целевым значением.

## Транзакция deployment

Скрипт выполняет следующие действия:

1. захватывает `flock`, запрещая параллельные deployment;
2. проверяет полный 40-символьный SHA и обязательные файлы;
3. формирует новый bundle во временном каталоге на том же файловом разделе;
4. проверяет наличие stage MCP allowlist и относительного redirect;
5. собирает кандидатный образ из подготовленного bundle во временном контексте.
   В копии Dockerfile пакет `libatomic1` устанавливается до запуска Playwright.
   Ошибка сборки оставляет текущий контейнер и его bundle на месте;
6. атомарно материализует `TAVILY_TOKEN` в отдельный файл `0600` и создаёт
   Compose override без значения секрета;
7. перемещает прежний `app-source` в timestamped backup и устанавливает новый;
8. записывает `app-source-sha.txt`;
9. назначает проверенный образ сервису Auditor;
10. выполняет `up -d --no-build --force-recreate auditor`;
11. ожидает Docker status `healthy`;
12. проверяет `/api/health` и совпадение возвращаемого SHA с целевым;
13. проверяет `/mcp → 307 Location: /mcp/`;
14. выполняет MCP `initialize` через `POST /mcp/`;
15. сохраняет deployment evidence как GitHub Actions artifact.

## Автоматический rollback

После переключения bundle ошибка recreate, health или smoke запускает rollback:

1. неуспешный bundle перемещается в `app-source.failed.*`;
2. предыдущий bundle возвращается из backup;
3. восстанавливается предыдущий `app-source-sha.txt`;
4. сервису возвращается сохранённый идентификатор предыдущего образа и
   контейнер пересоздаётся из него;
5. проверяется health восстановленного контейнера;
6. в Actions log выводятся последние 200 строк контейнера.

Rollback приложения не заменяет snapshot или восстановление всей VM. Такой уровень относится к SA-02.2/OpenStack.

## Ручной запуск из GitHub

`Actions → Deploy Stage → Run workflow`.

Поле `commit_sha`:

- можно оставить пустым, чтобы развернуть выбранный ref;
- можно указать полный 40-символьный SHA;
- короткий SHA отклоняется.

## Ручное аварийное восстановление на VPS

Посмотреть текущее состояние:

```bash
cd /opt/aimeton/auditor-stack
cat app-source-sha.txt
docker ps --filter name=aimeton-auditor
docker logs --tail 200 aimeton-auditor
find .deployments -maxdepth 1 -type d -name 'app-source.backup.*' -printf '%T@ %p\n' | sort -rn
```

Выбрать нужный backup и восстановить:

```bash
cd /opt/aimeton/auditor-stack
BACKUP="/opt/aimeton/auditor-stack/.deployments/app-source.backup.<timestamp>.<sha>"
FAILED="/opt/aimeton/auditor-stack/.deployments/app-source.manual-failed.$(date -u +%Y%m%dT%H%M%SZ)"

mv app-source "$FAILED"
mv "$BACKUP" app-source
# Записать полный SHA восстановленного bundle:
printf '%s\n' '<FULL_PREVIOUS_SHA>' > app-source-sha.txt

docker compose \
  -f docker-compose.yml \
  -f docker-compose.runtime-secrets.yml \
  build auditor
docker compose \
  -f docker-compose.yml \
  -f docker-compose.runtime-secrets.yml \
  up -d --force-recreate auditor
docker ps --filter name=aimeton-auditor
docker logs --tail 200 aimeton-auditor
```

Затем подтвердить:

```bash
curl -fsS https://stage-auditor.aimeton.ru/api/health
curl -sS -D - -o /dev/null --max-redirs 0 https://stage-auditor.aimeton.ru/mcp
```

## Evidence of Done для SA-02.1

После merge требуется фактический запуск на `main` и фиксация:

- Actions run `Baseline CI`: success;
- Actions run `Deploy Stage`: success;
- requested SHA равен `app-source-sha.txt`;
- контейнер `healthy`;
- `/api/health`: `200`;
- `/mcp`: `307`, относительный `Location: /mcp/`;
- MCP initialize: `200`;
- artifact `stage-deployment-<sha>-<run_id>`;
- контролируемый rollback test.

Дополнительно для provider-secret слоя:

- `.runtime-secrets.env` имеет mode `0600`;
- Compose override не содержит значение token;
- `/api/search/health` возвращает `secrets_exposed=false`;
- deployment log и artifact не содержат token.
