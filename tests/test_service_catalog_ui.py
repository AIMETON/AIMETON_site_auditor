from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_service_catalog_exposes_three_real_services_and_honest_beta() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "service-catalog.js").read_text(encoding="utf-8")

    for service in ("site-audit", "company-intelligence", "hunter", "interface-audit"):
        assert f'data-service-card="{service}"' in index

    assert 'data-service-card="interface-audit"' in index
    assert "disabled" in index
    assert "Beta" in index

    assert "/api/analyze/start" in (STATIC / "live-analysis.js").read_text(encoding="utf-8")
    assert "/api/company-intelligence" in script
    assert "/api/hunt" in script


def test_service_catalog_is_mobile_and_safe_by_construction() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    styles = (STATIC / "service-catalog.css").read_text(encoding="utf-8")
    script = (STATIC / "service-catalog.js").read_text(encoding="utf-8").lower()

    assert 'name="viewport"' in index
    assert "@media(max-width:720px)" in styles
    assert "credentials: 'same-origin'" in script
    assert "textcontent" in script

    for forbidden in ("chain-of-thought", "raw_prompt", "provider_payload", "access_token", "secret_key"):
        assert forbidden not in index.lower()
        assert forbidden not in script


def test_catalog_is_initial_experience_and_forms_open_only_after_selection() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "service-catalog.js").read_text(encoding="utf-8")

    for service in ("site-audit", "company-intelligence", "hunter"):
        assert f'data-service-panel="{service}" hidden' in index

    for action in ("Проверить сайт →", "Исследовать компанию →", "Найти клиентов →"):
        assert action in index

    assert "clearSelection();" in script
    assert "selectService('site-audit')" not in script
    assert "panel.hidden = true" in script


def test_service_cards_override_global_button_nowrap_and_keep_actions_aligned() -> None:
    styles = (STATIC / "service-catalog.css").read_text(encoding="utf-8")

    assert ".service-card{" in styles
    assert "white-space:normal" in styles
    assert "min-width:0" in styles
    assert "flex-direction:column" in styles
    assert ".service-card p{" in styles
    assert "overflow-wrap:anywhere" in styles
    assert ".service-card__action{display:block;margin-top:auto" in styles


def test_disabled_service_card_is_visually_secondary_not_broken() -> None:
    styles = (STATIC / "service-catalog.css").read_text(encoding="utf-8")

    assert '.service-card[disabled]{' in styles
    assert "opacity:.58" in styles
    assert "background:#f8f9fc" in styles
    assert "cursor:not-allowed" in styles


def test_hunter_results_are_grouped_for_commercial_scanability() -> None:
    script = (STATIC / "service-catalog.js").read_text(encoding="utf-8")
    styles = (STATIC / "service-catalog.css").read_text(encoding="utf-8")

    assert "function classifyCandidate(candidate)" in script
    assert "deep_analysis_performed === true" in script
    assert "Компания-кандидат" in script
    assert "Источник для проверки" in script
    assert "Наблюдение" in script
    assert "Источники для дополнительной проверки" in script
    assert "candidate_kind" not in script
    assert "function appendCandidate(container, candidate, fallbackRegion)" in script
    assert ".service-summary__candidate-top" in styles
    assert ".hunter-supporting-details" in styles
    assert ".hunter-candidate-action" in styles


def test_hunter_never_hides_returned_candidate_count_or_groups_by_default() -> None:
    script = (STATIC / "service-catalog.js").read_text(encoding="utf-8")

    assert "function candidateGroupCounts(candidates)" in script
    assert "const funnel = data.funnel || {};" in script
    assert "funnel.raw_results" in script
    assert "funnel.unique_candidates" in script
    assert "funnel.qualified_candidates" in script
    assert "funnel.returned_candidates" in script
    assert "Raw: ${rawResults} → уникальные: ${uniqueCandidates} → прошли фильтр: ${qualifiedCandidates} → возвращено API: ${returnedCandidates}" in script
    assert "Компании-кандидаты: ${counts.company}" in script
    assert "источники для проверки: ${counts.supporting}" in script
    assert "наблюдение: ${counts.observation}" in script
    assert "все возвращённые результаты раскрыты ниже" in script
    assert script.count("details.open = true;") == 2
    assert "отображено ${candidateCount} результатов" in script
    assert "/api/hunt/start?search_regime=" in script
    assert "X-Hunter-Run-Token" in script
    assert "Остановить поиск" in script


def test_hunter_desktop_results_have_visible_scroll_without_trapping_mobile() -> None:
    styles = (STATIC / "service-catalog.css").read_text(encoding="utf-8")

    assert "#hunterOutput .service-summary{max-height:min(62vh,720px);overflow-y:auto" in styles
    assert "scrollbar-gutter:stable" in styles
    assert "overscroll-behavior:contain" in styles
    assert "#hunterOutput .service-summary{max-height:none;overflow-y:visible;scrollbar-gutter:auto" in styles
