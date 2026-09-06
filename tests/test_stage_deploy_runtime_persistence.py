from pathlib import Path


DEPLOY = Path("scripts/deploy_stage.sh")
RECONCILE = Path(".github/workflows/runtime-persistence-reconcile.yml")


def test_transactional_deploy_includes_runtime_data_mount_before_container_start() -> None:
    text = DEPLOY.read_text(encoding="utf-8")

    assert 'DATA_DIR="${DATA_DIR:-$STACK_DIR/data/runtime-core}"' in text
    assert 'mkdir -p "$DATA_DIR"' in text
    assert 'chmod 700 "$DATA_DIR"' in text
    assert '      - ./data/runtime-core:/app/data' in text
    assert '      AIMETON_RUNTIME_DB: "/app/data/runtime-core.sqlite3"' in text
    assert '      AIMETON_AUTH_DB: "/app/data/auth.sqlite3"' in text

    configure_pos = text.index("configure_runtime_secrets")
    switch_pos = text.index('log "Switching bundle atomically')
    assert configure_pos < switch_pos


def test_transactional_deploy_refuses_to_pass_without_live_app_data_mount() -> None:
    text = DEPLOY.read_text(encoding="utf-8")

    assert 'verify_runtime_persistence_mount()' in text
    assert 'eq .Destination "/app/data"' in text
    assert 'runtime persistence mount missing from deployed container' in text
    assert 'verify_runtime_persistence_mount || rollback' in text


def test_reconcile_remains_idempotent_safety_net_not_primary_mount_creator() -> None:
    text = RECONCILE.read_text(encoding="utf-8")

    assert "expected_volume='./data/runtime-core:/app/data'" in text
    assert "mode='verify-only / no mutation required'" in text
    assert 'if [[ "$compose_correct" != yes || "$mount_correct" != yes ]]; then' in text
    assert 'if [[ "$mutation_required" == yes ]]; then' in text
    assert 'up -d --force-recreate "$SERVICE"' in text
    assert "verify-only path completed with zero container recreation" in text
