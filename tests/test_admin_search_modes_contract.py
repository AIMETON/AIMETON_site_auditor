from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_workspace_exposes_guided_search_modes() -> None:
    html = (ROOT / "static" / "admin-workspace.html").read_text(encoding="utf-8")
    assert 'id="search-mode-preset"' in html
    assert 'value="fast"' in html
    assert 'value="balanced"' in html
    assert 'value="quality"' in html
    assert 'value="coverage"' in html
    assert 'id="apply-search-mode"' in html


def test_guided_modes_compile_to_existing_search_gateway_strategies() -> None:
    js = (ROOT / "static" / "admin-search-strategies.js").read_text(encoding="utf-8")
    assert "fallback_first_nonempty" in js
    assert "cascade_until_target" in js
    assert "consensus_union" in js
    assert "exhaustive_coverage" in js
    assert "SearchGateway" in js
    assert "Cost/budget guards" in js


def test_guided_mode_requires_existing_save_path() -> None:
    js = (ROOT / "static" / "admin-search-strategies.js").read_text(encoding="utf-8")
    assert "strategySelect.value = mode.strategy" in js
    assert "fetch('/api/admin/search-strategies'" in js
    assert "method: 'PUT'" in js
    assert "Применить режим к активному профилю" in (ROOT / "static" / "admin-workspace.html").read_text(encoding="utf-8")
