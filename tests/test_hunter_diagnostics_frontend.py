from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_JS = ROOT / "static" / "hunter-diagnostics.js"
LOADER_JS = ROOT / "static" / "hunter-company-handoff-context.js"


def test_hunter_diagnostics_frontend_contract_is_admin_scoped_and_tabbed() -> None:
    source = DIAGNOSTICS_JS.read_text(encoding="utf-8")
    for marker in (
        "/api/auth/me",
        "/api/runtime/hunter-diagnostics/attempts",
        "query_plan",
        "providers",
        "raw_intake",
        "qualification",
        "deep_audit",
        "funnel_trace",
        "Режим диагностики (администратор)",
    ):
        assert marker in source
    assert "innerHTML" not in source
    assert "textContent" in source


def test_hunter_diagnostics_loader_is_wired_from_existing_hunter_extension() -> None:
    source = LOADER_JS.read_text(encoding="utf-8")
    assert "/static/hunter-diagnostics.js" in source
    assert "data-hunter-diagnostics-loader" in source or "hunterDiagnosticsLoader" in source


def test_hunter_diagnostics_javascript_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runtime is not available in this baseline environment")
    result = subprocess.run(
        [node, "--check", str(DIAGNOSTICS_JS)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
