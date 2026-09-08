from pathlib import Path

BASELINE = Path(".github/workflows/baseline-ci.yml")
PROJECT_SYNC = Path(".github/workflows/project-status-sync.yml")
GOVERNANCE = Path(".github/workflows/acceptance-governance.yml")
RUNNER_PROOF = Path(".github/workflows/baseline-runner-infrastructure-proof.yml")


def test_repo_wide_pr_checks_are_github_hosted() -> None:
    baseline = BASELINE.read_text(encoding="utf-8")
    project = PROJECT_SYNC.read_text(encoding="utf-8")
    governance = GOVERNANCE.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-24.04" in baseline
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" not in baseline
    assert "runs-on: ubuntu-24.04" in project
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" not in project
    assert governance.count("runs-on: ubuntu-24.04") == 3
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" not in governance


def test_machine_specific_runner_proof_is_dispatch_only() -> None:
    proof = RUNNER_PROOF.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in proof
    assert "pull_request:" not in proof
    assert "push:" not in proof
    assert "runs-on: [self-hosted, Linux, X64, stage, auditor]" in proof
    assert "aimeton-site-auditor-stage" in proof
    assert "aimeton-main-server" in proof
    assert "/mnt/aimeton-dependency-cache" in proof
