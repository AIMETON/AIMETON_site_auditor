from pathlib import Path


ROUTER_WORKFLOW = Path(".github/workflows/aimeton-command-router.yml")
ROUTER = Path("scripts/aimeton_command_router.py")


def test_command_router_is_the_sanitized_single_ingress_contract() -> None:
    workflow = ROUTER_WORKFLOW.read_text(encoding="utf-8")
    router = ROUTER.read_text(encoding="utf-8")

    assert "issue_comment:" in workflow
    assert "actions: write" in workflow
    assert "github.event.comment.user.login == 'Dimar4713'" in workflow
    assert "group: aimeton-command-router-${{ github.event.issue.number }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "python3 scripts/aimeton_command_router.py" in workflow
    assert "runs-on: ubuntu-24.04" in workflow

    assert "unsupported_or_invalid_command" in router
    assert "ignored: unsupported_or_invalid_command" in router
    assert "raise RuntimeError(f\"Command {command} is not authorised on issue {issue_number}\")" in router

    for command, issue_number, workflow_id in (
        ("deploy-stage", 337, "deploy-stage.yml"),
        ("validate-baseline-self-hosted", 767, "baseline-self-hosted-dispatch.yml"),
        ("preflight-accb-layer-b-tokenizers", 798, "accb-layer-b-tokenizer-preflight.yml"),
        ("probe-accb-openrouter-edge-large-body", 820, "accb-openrouter-edge-large-body-probe.yml"),
        ("probe-accb-openrouter-edge-http", 820, "accb-openrouter-edge-large-body-probe.yml"),
        ("probe-accb-openrouter-edge-github", 820, "accb-openrouter-edge-large-body-probe.yml"),
        ("accept-admin-trace-stage", 293, "accept-admin-trace-stage.yml"),
        ("accept-aimeton-self-audit-stage", 293, "accept-aimeton-self-audit-stage.yml"),
        ("accept-checkpoint-stage", 88, "accept-checkpoint-stage.yml"),
        ("accept-mobile-ui-stage", 223, "accept-mobile-ui-stage.yml"),
        ("accept-service-catalog-stage", 274, "accept-service-catalog-stage.yml"),
        ("accept-logging-pressure-stage", 293, "accept-logging-pressure-stage.yml"),
        ("accept-live-analysis-stage", 270, "accept-live-analysis-stage.yml"),
        ("accept-interface-audit-stage", 291, "interface-audit-stage-acceptance.yml"),
        ("accept-ui-stage", 262, "ui-stage-visual-audit.yml"),
        ("accept-user-workspace-stage", 192, "stage-user-workspace-acceptance.yml"),
        ("accept-admin-workspace-stage", 197, "stage-admin-workspace-acceptance.yml"),
        ("accept-mission-stage-v2", 177, "stage-mission-ownership-acceptance-v2.yml"),
        ("accept-integrated-stage-continuity", 203, "stage-integrated-continuity.yml"),
        ("accept-integrated-stage-core", 203, "stage-integrated-acceptance.yml"),
        ("audit-server-architecture", 36, "server-architecture-audit.yml"),
        ("accept-integrated-stage-companies-bootstrap", 203, "stage-real-company-bootstrap-acceptance.yml"),
        ("audit-auth-persistence", 159, "server-auth-persistence-audit.yml"),
        ("diagnose-mission-stage", 177, "stage-mission-diagnostics.yml"),
        ("reconcile-stage-data-mount", 177, "stage-data-mount-reconcile.yml"),
        ("repair-admin-stage", 164, "repair-stage-admin.yml"),
        ("accept-auth-stage", 164, "stage-auth-acceptance.yml"),
    ):
        assert f'"{command}": ({issue_number}, "{workflow_id}"' in router

    for ledger_field in (
        "- command:",
        "- target workflow:",
        "- issue:",
        "- actor:",
        "- exact SHA:",
        "- result: `dispatched`",
    ):
        assert ledger_field in router


def test_router_requires_exact_sha_and_dispatches_main_only() -> None:
    text = ROUTER.read_text(encoding="utf-8")
    assert "([0-9a-f]{40})" in text
    assert 'api("GET", f"commits/{sha}")' in text
    assert 'f"actions/workflows/{workflow_id}/dispatches"' in text
    assert 'payload={"ref": "main", "inputs": inputs}' in text


def _assert_dispatch_only(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "issue_comment:" not in trigger_block
    assert "github.event_name == 'issue_comment'" not in text
    assert "inputs.expected_sha" in text


def test_checkpoint_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-checkpoint-stage.yml")


def test_mobile_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-mobile-ui-stage.yml")


def test_admin_trace_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-admin-trace-stage.yml")


def test_self_audit_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-aimeton-self-audit-stage.yml")


def test_service_catalog_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-service-catalog-stage.yml")


def test_logging_pressure_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-logging-pressure-stage.yml")


def test_live_analysis_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/accept-live-analysis-stage.yml")


def test_interface_audit_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/interface-audit-stage-acceptance.yml")


def test_ui_visual_audit_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/ui-stage-visual-audit.yml")


def test_user_workspace_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-user-workspace-acceptance.yml")


def test_admin_workspace_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-admin-workspace-acceptance.yml")


def test_mission_ownership_v2_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-mission-ownership-acceptance-v2.yml")


def test_integrated_continuity_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-integrated-continuity.yml")


def test_integrated_core_no_longer_subscribes_to_comments() -> None:
    text = Path(".github/workflows/stage-integrated-acceptance.yml").read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "issue_comment:" not in trigger_block
    assert "github.event_name == 'issue_comment'" not in text
    assert "inputs.expected_sha" in text
    assert "requested_sha='${{ inputs.expected_sha }}'" in text
    assert 'acceptance_contract_sha="$GITHUB_SHA"' in text
    assert '[[ "$requested_sha" == "$acceptance_contract_sha" ]]' in text


def test_server_architecture_audit_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/server-architecture-audit.yml")


def test_real_company_bootstrap_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-real-company-bootstrap-acceptance.yml")


def test_auth_persistence_audit_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/server-auth-persistence-audit.yml")


def test_mission_diagnostics_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-mission-diagnostics.yml")


def test_stage_data_mount_reconcile_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-data-mount-reconcile.yml")


def test_stage_admin_repair_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/repair-stage-admin.yml")
    text = Path(".github/workflows/repair-stage-admin.yml").read_text(encoding="utf-8")
    assert "Verify exact deployed SHA before mutation" in text
    assert "requested SHA does not match deployed SHA" in text


def test_stage_auth_acceptance_no_longer_subscribes_to_comments() -> None:
    _assert_dispatch_only(".github/workflows/stage-auth-acceptance.yml")
    text = Path(".github/workflows/stage-auth-acceptance.yml").read_text(encoding="utf-8")
    assert "Verify exact deployed SHA before acceptance mutation" in text
    assert "Refusing acceptance mutation: requested SHA does not match deployed SHA" in text


def test_only_router_and_optional_status_sync_subscribe_to_issue_comments() -> None:
    workflow_dir = Path(".github/workflows")
    allowed = {"aimeton-command-router.yml", "project-status-sync.yml"}
    subscribers = {
        path.name
        for path in workflow_dir.glob("*.yml")
        if "issue_comment:" in path.read_text(encoding="utf-8").split("permissions:", 1)[0]
    }
    assert "aimeton-command-router.yml" in subscribers
    assert not (subscribers - allowed), f"unexpected direct issue_comment subscribers: {sorted(subscribers - allowed)}"


def test_superseded_mission_ownership_v1_does_not_return() -> None:
    assert not Path(".github/workflows/stage-mission-ownership-acceptance.yml").exists()
