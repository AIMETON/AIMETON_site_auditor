from __future__ import annotations

from pathlib import Path


WORKFLOW_EXPECTED_RETRY_BLOCKS = {
    ".github/workflows/deploy-stage.yml": 1,
    ".github/workflows/configure-dadata-stage.yml": 2,
    ".github/workflows/runtime-persistence-reconcile.yml": 1,
    ".github/workflows/stage-convergence.yml": 2,
    ".github/workflows/stage-auth-persistence-guard.yml": 1,
}


def test_stage_workflows_reuse_exact_local_commit_and_bound_network_fetches():
    for path, expected_blocks in WORKFLOW_EXPECTED_RETRY_BLOCKS.items():
        text = Path(path).read_text(encoding="utf-8")
        assert text.count('git cat-file -e "') >= expected_blocks, path
        assert text.count("for attempt in 1 2 3 4 5; do") >= expected_blocks, path
        assert text.count("fetched=false") >= expected_blocks, path
        assert text.count('git fetch --no-tags --depth=1 origin "') >= expected_blocks, path
        assert text.count('test "$(git rev-parse HEAD)" = "') >= expected_blocks, path
        assert "x-access-token:" in text, path
