from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static" / "hunter-company-handoff-context.js"


def test_hunter_ui_labels_search_geography_as_requested_not_confirmed() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "Искомый регион: ${region}" in source
    assert "rewriteHunterRegionLabels" in source
    assert "replace(/^Регион:/, 'Искомый регион:')" in source
    assert "parts.push(`Регион: ${region}`)" not in source


def test_hunter_region_honesty_javascript_syntax_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runtime is not available in this baseline environment")
    result = subprocess.run(
        [node, "--check", str(SOURCE)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
