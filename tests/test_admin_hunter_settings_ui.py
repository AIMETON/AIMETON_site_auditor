from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_workspace_exposes_search_strategy_and_active_tariff_controls() -> None:
    html = (ROOT / "static" / "admin-workspace.html").read_text(encoding="utf-8")
    strategy_script = (ROOT / "static" / "admin-search-strategies.js").read_text(encoding="utf-8")
    hunter_script = (ROOT / "static" / "admin-hunter-settings.js").read_text(encoding="utf-8")

    assert 'id="search-strategy-title">Режим поиска<' in html
    assert 'id="search-quality-mode"' in html
    assert 'value="economy">Экономный' in html
    assert 'value="balanced">Сбалансированный' in html
    assert 'value="quality">Качество' in html
    assert 'value="maximum">Максимальный охват' in html
    assert 'id="apply-search-quality-mode"' in html
    assert 'id="search-active-tariff"' in html
    assert 'id="search-default-strategy"' in html
    assert 'id="search-enabled-providers"' in html
    assert 'id="search-canonical-provider-order"' in html
    assert 'id="search-paid-policy"' in html
    assert 'id="search-paid-fanout-policy"' in html
    assert 'id="search-tariff-profiles"' in html
    assert 'id="search-strategy-catalog"' in html
    assert 'SearXNG (self-hosted)' in html
    assert 'Yandex Search' in html
    assert 'Tavily' in html
    assert '/api/admin/search-strategies' in strategy_script
    assert "'X-CSRF-Token': csrfToken()" in strategy_script
    assert 'canonical_provider_order: parseProviderOrder(canonicalProviderOrder.value)' in strategy_script
    assert 'Наследовать глобальную стратегию' in strategy_script
    assert 'только global/debug' in strategy_script
    assert 'tariffSafeOnly: true' in strategy_script
    assert "strategy: 'adaptive_cost_quality'" in strategy_script
    assert "strategy: 'consensus_union'" in strategy_script
    assert "strategy: 'exhaustive_coverage'" in strategy_script
    assert 'applyPresetToActiveProfile' in strategy_script

    assert 'id="hunter-settings-title">Параметры активного Hunter-профиля<' in html
    assert 'id="hunter-settings-form"' in html
    assert 'id="hunter-setting-max-queries"' in html
    assert 'id="hunter-setting-results-per-query"' in html
    assert 'id="hunter-setting-max-candidates"' in html
    assert 'id="hunter-setting-output-limit"' in html
    assert 'id="hunter-setting-minimum-pre-score"' in html
    assert 'id="hunter-setting-deep-audit-score"' in html
    assert 'id="hunter-setting-concurrency"' in html
    assert '/api/admin/hunter-settings' in hunter_script


def test_user_hunter_form_no_longer_hardcodes_operational_limits() -> None:
    script = (ROOT / "static" / "service-catalog.js").read_text(encoding="utf-8")
    hunter_block = script.split("const huntForm =", 1)[1]

    assert "max_queries:" not in hunter_block
    assert "results_per_query:" not in hunter_block
    assert "max_candidates:" not in hunter_block
    assert "output_limit:" not in hunter_block
    assert "concurrency:" not in hunter_block
    assert "const candidates = data.candidates || [];" in hunter_block
    assert "renderHunterCandidates(list, candidates," in hunter_block
