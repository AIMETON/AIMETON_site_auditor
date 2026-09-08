from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATIC = ROOT / ".github" / "workflows" / "baseline-ci.yml"
DISPATCH = ROOT / ".github" / "workflows" / "baseline-self-hosted-dispatch.yml"
ROUTER = ROOT / "scripts" / "aimeton_command_router.py"


def test_baseline_event_authorities_are_split() -> None:
    automatic = AUTOMATIC.read_text(encoding="utf-8")
    dispatch = DISPATCH.read_text(encoding="utf-8")

    assert "pull_request:" in automatic
    assert "push:" in automatic
    assert "workflow_dispatch:" not in automatic

    assert "workflow_dispatch:" in dispatch
    assert "pull_request:" not in dispatch
    assert "push:" not in dispatch


def test_exact_sha_command_routes_to_dedicated_dispatch_workflow() -> None:
    router = ROUTER.read_text(encoding="utf-8")

    expected = (
        '"validate-baseline-self-hosted": '
        '(767, "baseline-self-hosted-dispatch.yml", '
        '{"expected_sha": "{sha}", "evidence_issue": "767"})'
    )
    assert expected in router


def test_baseline_runner_authorities_are_split_and_marketplace_free() -> None:
    automatic = AUTOMATIC.read_text(encoding="utf-8")
    dispatch = DISPATCH.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-24.04" in automatic
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" not in automatic
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" in dispatch

    for content in (automatic, dispatch):
        assert "actions/checkout@" not in content
        assert "actions/setup-python@" not in content
        assert "actions/upload-artifact@" not in content


def test_baseline_job_env_does_not_use_runner_context_before_job_assignment() -> None:
    for path in (AUTOMATIC, DISPATCH):
        content = path.read_text(encoding="utf-8")
        assert "VENV_DIR: ${{ runner." not in content
        assert "VENV_DIR: /tmp/aimeton-baseline-${{ github.run_id }}" in content
