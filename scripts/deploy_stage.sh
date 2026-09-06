#!/usr/bin/env bash
set -Eeuo pipefail

STACK_DIR="${STACK_DIR:-/opt/aimeton/auditor-stack}"
SOURCE_DIR="${SOURCE_DIR:-${GITHUB_WORKSPACE:-$PWD}}"
DEPLOY_SHA="${DEPLOY_SHA:-${GITHUB_SHA:-}}"
STAGE_URL="${STAGE_URL:-https://stage-auditor.aimeton.ru}"
SERVICE="${SERVICE:-auditor}"
CONTAINER="${CONTAINER:-aimeton-auditor}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
BACKUP_KEEP="${BACKUP_KEEP:-5}"
FAILED_KEEP="${FAILED_KEEP:-2}"
LOCK_FILE="${LOCK_FILE:-/tmp/aimeton-auditor-stage-deploy.lock}"
TAVILY_TOKEN="${TAVILY_TOKEN:-}"
YANDEX_SEARCH_API_KEY="${YANDEX_SEARCH_API_KEY:-}"
YANDEX_CLOUD_FOLDER_ID="${YANDEX_CLOUD_FOLDER_ID:-${YANDEX_SEARCH_FOLDER_ID:-}}"
TAVILY_SEARCH_COST_USD="${TAVILY_SEARCH_COST_USD:-0.008}"
YANDEX_SEARCH_COST_RUB="${YANDEX_SEARCH_COST_RUB:-0.01}"
SEARCH_MISSION_BUDGET_USD="${SEARCH_MISSION_BUDGET_USD:-0.008}"
SEARCH_MISSION_BUDGET_RUB="${SEARCH_MISSION_BUDGET_RUB:-0.01}"
SEARCH_QUOTA_TAVILY="${SEARCH_QUOTA_TAVILY:-10}"
SEARCH_QUOTA_YANDEX="${SEARCH_QUOTA_YANDEX:-10}"
SEARCH_PROVIDER_ORDER="${SEARCH_PROVIDER_ORDER:-yandex,searxng,tavily}"
SEARCH_ALLOW_PAID_FALLBACK="${SEARCH_ALLOW_PAID_FALLBACK:-false}"
SEARCH_EFFECTIVENESS_DEBUG="${SEARCH_EFFECTIVENESS_DEBUG:-false}"
IDENTITY_SEARCH_PROVIDER_ORDER="${IDENTITY_SEARCH_PROVIDER_ORDER:-yandex,tavily,searxng}"
IDENTITY_SEARCH_ALLOW_PAID_FALLBACK="${IDENTITY_SEARCH_ALLOW_PAID_FALLBACK:-true}"
IDENTITY_AUTHORITY_HOSTS="${IDENTITY_AUTHORITY_HOSTS:-egrul.nalog.ru,service.nalog.ru,bo.nalog.ru}"

log() {
  printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

[[ -n "$DEPLOY_SHA" ]] || fail "DEPLOY_SHA is required"
[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "DEPLOY_SHA must be a full 40-character commit SHA"
[[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]] || fail "BACKUP_KEEP must be an integer"
[[ "$FAILED_KEEP" =~ ^[0-9]+$ ]] || fail "FAILED_KEEP must be an integer"
[[ -d "$STACK_DIR" ]] || fail "Stack directory not found: $STACK_DIR"
[[ -f "$STACK_DIR/docker-compose.yml" ]] || fail "docker-compose.yml not found in $STACK_DIR"
[[ -d "$SOURCE_DIR" ]] || fail "Source directory not found: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/app/main.py" ]] || fail "app/main.py missing from source"
[[ -f "$SOURCE_DIR/app/mcp_server.py" ]] || fail "app/mcp_server.py missing from source"
[[ -f "$SOURCE_DIR/requirements.txt" ]] || fail "requirements.txt missing from source"
[[ -n "$TAVILY_TOKEN" ]] || fail "TAVILY_TOKEN is required for stage"
[[ "$TAVILY_TOKEN" != *$'\n'* ]] || fail "TAVILY_TOKEN contains a newline"
[[ -n "$YANDEX_SEARCH_API_KEY" ]] || fail "YANDEX_SEARCH_API_KEY is required for stage"
[[ "$YANDEX_SEARCH_API_KEY" != *$'\n'* ]] || fail "YANDEX_SEARCH_API_KEY contains a newline"
[[ -n "$YANDEX_CLOUD_FOLDER_ID" ]] || fail "YANDEX_CLOUD_FOLDER_ID is required for stage"
[[ "$TAVILY_SEARCH_COST_USD" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "TAVILY_SEARCH_COST_USD must be a non-negative decimal"
[[ "$YANDEX_SEARCH_COST_RUB" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "YANDEX_SEARCH_COST_RUB must be a non-negative decimal"
[[ "$SEARCH_MISSION_BUDGET_USD" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "SEARCH_MISSION_BUDGET_USD must be a non-negative decimal"
[[ "$SEARCH_MISSION_BUDGET_RUB" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "SEARCH_MISSION_BUDGET_RUB must be a non-negative decimal"
[[ "$SEARCH_QUOTA_TAVILY" =~ ^[1-9][0-9]*$ ]] || fail "SEARCH_QUOTA_TAVILY must be a positive integer"
[[ "$SEARCH_QUOTA_YANDEX" =~ ^[1-9][0-9]*$ ]] || fail "SEARCH_QUOTA_YANDEX must be a positive integer"

exec 9>"$LOCK_FILE"
flock -n 9 || fail "Another stage deployment is already running"

DEPLOY_ROOT="$STACK_DIR/.deployments"
STAGING_DIR="$DEPLOY_ROOT/app-source.$DEPLOY_SHA.$$.staging"
BACKUP_DIR="$DEPLOY_ROOT/app-source.backup.$(date -u +'%Y%m%dT%H%M%SZ').${DEPLOY_SHA:0:12}"
FAILED_DIR="$DEPLOY_ROOT/app-source.failed.$(date -u +'%Y%m%dT%H%M%SZ').${DEPLOY_SHA:0:12}"
CURRENT_DIR="$STACK_DIR/app-source"
SHA_FILE="$STACK_DIR/app-source-sha.txt"
RUNTIME_SECRETS_FILE="$STACK_DIR/.runtime-secrets.env"
COMPOSE_OVERRIDE_FILE="$STACK_DIR/docker-compose.runtime-secrets.yml"
DATA_DIR="${DATA_DIR:-$STACK_DIR/data/runtime-core}"
PREVIOUS_SHA="unknown"
SWITCHED=0

mkdir -p "$DEPLOY_ROOT"

configure_runtime_secrets() {
  local secret_tmp override_tmp
  secret_tmp="$(mktemp "$DEPLOY_ROOT/runtime-secrets.XXXXXX")"
  override_tmp="$(mktemp "$DEPLOY_ROOT/runtime-compose.XXXXXX")"
  chmod 600 "$secret_tmp"
  mkdir -p "$DATA_DIR"
  chmod 700 "$DATA_DIR"
  {
    printf 'TAVILY_TOKEN=%s\n' "$TAVILY_TOKEN"
    printf 'YANDEX_SEARCH_API_KEY=%s\n' "$YANDEX_SEARCH_API_KEY"
    if [[ -f "$RUNTIME_SECRETS_FILE" ]]; then
      grep -E '^(DADATA_API|DADATA_SECRET|DADATA_TOKEN|DADATA_BACKOFF_BASE_SECONDS|DADATA_BACKOFF_MAX_SECONDS)=' \
        "$RUNTIME_SECRETS_FILE" || true
    fi
  } > "$secret_tmp"
  cat > "$override_tmp" <<EOF
services:
  $SERVICE:
    env_file:
      - $RUNTIME_SECRETS_FILE
    environment:
      YANDEX_CLOUD_FOLDER_ID: "$YANDEX_CLOUD_FOLDER_ID"
      TAVILY_SEARCH_COST_USD: "$TAVILY_SEARCH_COST_USD"
      YANDEX_SEARCH_COST_RUB: "$YANDEX_SEARCH_COST_RUB"
      SEARCH_MISSION_BUDGET_USD: "$SEARCH_MISSION_BUDGET_USD"
      SEARCH_MISSION_BUDGET_RUB: "$SEARCH_MISSION_BUDGET_RUB"
      SEARCH_QUOTA_TAVILY: "$SEARCH_QUOTA_TAVILY"
      SEARCH_QUOTA_YANDEX: "$SEARCH_QUOTA_YANDEX"
      SEARCH_PROVIDER_ORDER: "$SEARCH_PROVIDER_ORDER"
      SEARCH_ALLOW_PAID_FALLBACK: "$SEARCH_ALLOW_PAID_FALLBACK"
      SEARCH_EFFECTIVENESS_DEBUG: "$SEARCH_EFFECTIVENESS_DEBUG"
      AIMETON_RUNTIME_DB: "/app/data/runtime-core.sqlite3"
      AIMETON_AUTH_DB: "/app/data/runtime-core.sqlite3"
      SEARCH_CACHE_DB_PATH: "/app/data/search-cache.sqlite3"
      SEARCH_CACHE_MAX_ENTRIES: "4096"
      IDENTITY_SEARCH_PROVIDER_ORDER: "$IDENTITY_SEARCH_PROVIDER_ORDER"
      IDENTITY_SEARCH_ALLOW_PAID_FALLBACK: "$IDENTITY_SEARCH_ALLOW_PAID_FALLBACK"
      IDENTITY_AUTHORITY_HOSTS: "$IDENTITY_AUTHORITY_HOSTS"
    volumes:
      - ./data/runtime-core:/app/data
EOF
  chmod 600 "$override_tmp"
  mv "$secret_tmp" "$RUNTIME_SECRETS_FILE"
  mv "$override_tmp" "$COMPOSE_OVERRIDE_FILE"
  log "Runtime provider configuration and persistent /app/data mount installed; existing DaData material preserved without exposing credentials"
}

verify_runtime_persistence_mount() {
  local mount
  mount="$(docker inspect "$CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Type}}|{{.Source}}|{{.Destination}}{{end}}{{end}}' 2>/dev/null || true)"
  [[ -n "$mount" ]] || { log "runtime persistence mount missing from deployed container"; return 1; }
  [[ "${mount##*|}" == "/app/data" ]] || { log "unexpected runtime persistence destination: $mount"; return 1; }
  log "Runtime persistence mount verified: $mount"
}

compose_stack() {
  (
    cd "$STACK_DIR"
    docker compose \
      -f docker-compose.yml \
      -f "$COMPOSE_OVERRIDE_FILE" \
      "$@"
  )
}

if [[ -f "$SHA_FILE" ]]; then
  PREVIOUS_SHA="$(tr -d '[:space:]' < "$SHA_FILE")"
fi

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  local status

  while (( SECONDS < deadline )); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
    log "Container status: ${status:-not-found}"
    if [[ "$status" == "healthy" ]]; then
      return 0
    fi
    if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
      return 1
    fi
    sleep 5
  done

  return 1
}

smoke_test() {
  local headers body status location

  log "Smoke: GET /api/health"
  body="$(curl --fail --silent --show-error --max-time 30 "$STAGE_URL/api/health")"
  python3 - "$body" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
assert payload.get("status") == "ok", payload
deployment_sha = payload.get("deployment_sha")
assert deployment_sha and len(deployment_sha) == 40, payload
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY

  log "Smoke: /mcp relative redirect"
  headers="$(mktemp)"
  status="$(curl --silent --show-error --max-time 30 --max-redirs 0 -D "$headers" -o /dev/null -w '%{http_code}' "$STAGE_URL/mcp" || true)"
  [[ "$status" == "307" ]] || { cat "$headers"; rm -f "$headers"; return 1; }
  location="$(awk 'BEGIN{IGNORECASE=1} /^location:/ {gsub("\\r", "", $2); print $2}' "$headers" | tail -n 1)"
  cat "$headers"
  rm -f "$headers"
  [[ "$location" == "/mcp/" ]] || { log "Unexpected Location: $location"; return 1; }

  log "Smoke: MCP initialize"
  body="$(curl --fail --silent --show-error --max-time 30 \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"stage-deploy-smoke","version":"1.0"}}}' \
    "$STAGE_URL/mcp/")"
  python3 - "$body" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
assert payload.get("jsonrpc") == "2.0", payload
assert payload.get("id") == 1, payload
result = payload.get("result") or {}
assert result.get("protocolVersion"), payload
assert (result.get("serverInfo") or {}).get("name") == "AIMETON Site Auditor", payload
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
}

rollback() {
  local reason="$1"
  log "ROLLBACK: $reason"

  if (( SWITCHED == 1 )) && [[ -d "$BACKUP_DIR" ]]; then
    if [[ -d "$CURRENT_DIR" ]]; then
      mv "$CURRENT_DIR" "$FAILED_DIR"
    fi
    mv "$BACKUP_DIR" "$CURRENT_DIR"
    printf '%s\n' "$PREVIOUS_SHA" > "$SHA_FILE"

    compose_stack build "$SERVICE"
    compose_stack up -d --force-recreate "$SERVICE"

    if wait_healthy; then
      log "Rollback restored SHA: $PREVIOUS_SHA"
    else
      log "CRITICAL: rollback container did not become healthy"
    fi
  else
    log "Rollback skipped: bundle was not switched"
  fi

  docker logs --tail 200 "$CONTAINER" || true
  exit 1
}

cleanup_by_count() {
  local pattern="$1"
  local keep="$2"
  local label="$3"
  local entry path rank=0

  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    path="${entry#* }"
    rank=$((rank + 1))
    if (( rank <= keep )); then
      log "Retention protect $label #$rank: $path"
      continue
    fi
    if [[ -f "$path/.retention-protected" ]]; then
      log "Retention marker protects $label: $path"
      continue
    fi
    [[ "$(readlink -f "$path")" == "$DEPLOY_ROOT"/* ]] || fail "Unsafe retention path: $path"
    log "Auto-removing excess $label bundle: $path"
    rm -rf -- "$path"
  done < <(find "$DEPLOY_ROOT" -maxdepth 1 -type d -name "$pattern" -printf '%T@ %p\n' | sort -rn)
}

trap 'rollback "unexpected failure at line $LINENO"' ERR

log "Preparing deployment SHA $DEPLOY_SHA from $SOURCE_DIR"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

tar \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='*.pyc' \
  -C "$SOURCE_DIR" -cf - . | tar -C "$STAGING_DIR" -xf -

[[ -f "$STAGING_DIR/app/main.py" ]] || fail "Staged app/main.py missing"
[[ -f "$STAGING_DIR/app/mcp_server.py" ]] || fail "Staged app/mcp_server.py missing"
[[ -f "$STAGING_DIR/requirements.txt" ]] || fail "Staged requirements.txt missing"

printf '%s\n' "$DEPLOY_SHA" > "$STAGING_DIR/.aimeton-deploy-sha"
[[ "$(tr -d '[:space:]' < "$STAGING_DIR/.aimeton-deploy-sha")" == "$DEPLOY_SHA" ]] || fail "Staged deployment identity mismatch"

grep -q 'stage-auditor.aimeton.ru' "$STAGING_DIR/app/mcp_server.py" || fail "Stage MCP host allowlist missing"
grep -q 'Location.*\/mcp\/' "$STAGING_DIR/app/main.py" || fail "Relative MCP redirect implementation missing"

configure_runtime_secrets
compose_stack config --quiet

log "Switching bundle atomically; previous SHA: $PREVIOUS_SHA"
if [[ -d "$CURRENT_DIR" ]]; then
  mv "$CURRENT_DIR" "$BACKUP_DIR"
fi
mv "$STAGING_DIR" "$CURRENT_DIR"
printf '%s\n' "$DEPLOY_SHA" > "$SHA_FILE"
SWITCHED=1

log "Building and recreating Docker service: $SERVICE"
compose_stack build "$SERVICE"
compose_stack up -d --force-recreate "$SERVICE"

wait_healthy || rollback "container failed health check"
verify_runtime_persistence_mount || rollback "runtime persistence mount missing from deployed container"
smoke_test || rollback "stage smoke test failed"

trap - ERR

DEPLOYED_SHA="$(tr -d '[:space:]' < "$SHA_FILE")"
[[ "$DEPLOYED_SHA" == "$DEPLOY_SHA" ]] || rollback "deployed SHA mismatch: $DEPLOYED_SHA"

log "DEPLOYMENT PASS"
log "Deployed SHA: $DEPLOYED_SHA"
log "Previous SHA: $PREVIOUS_SHA"
log "Backup: $BACKUP_DIR"
log "Search effectiveness debug: $SEARCH_EFFECTIVENESS_DEBUG"
log "Automatic retention thresholds: backups=$BACKUP_KEEP failed=$FAILED_KEEP"

docker ps --filter "name=$CONTAINER" --format 'container={{.Names}} image={{.Image}} status={{.Status}}'

cleanup_by_count 'app-source.backup.*' "$BACKUP_KEEP" 'backup'
cleanup_by_count 'app-source.failed.*' "$FAILED_KEEP" 'failed'
cleanup_by_count 'app-source.manual-failed.*' "$FAILED_KEEP" 'manual-failed'
