from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_legacy_result_compat_is_loaded_after_workspace() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/static/workspace-legacy-result-compat.js" in index
    assert index.index("/static/business-audit-workspace.js") < index.index("/static/workspace-legacy-result-compat.js")


def test_async_completion_hides_only_legacy_report_body_and_patches_workspace() -> None:
    script = (STATIC / "workspace-legacy-result-compat.js").read_text(encoding="utf-8")
    assert "aimeton:analysis-complete" in script
    assert "#resultInner" in script
    assert "setAttribute('hidden', '')" in script
    assert "patchBusinessWorkspace" in script
    assert "Оценка не рассчитана" in script
    assert "commercial_score_available" in script
    assert "#result'" not in script
    assert "#chat" not in script
    assert "setInterval(" not in script


def test_history_persists_only_compact_index_and_restores_from_durable_status() -> None:
    script = (STATIC / "workspace-legacy-result-compat.js").read_text(encoding="utf-8")
    compact_start = script.index("function compactHistoryEntry")
    compact_end = script.index("function persistHistory")
    compact = script[compact_start:compact_end]
    assert "analysis_id" in compact
    assert "mission_id" in compact
    assert "business_summary" in compact
    assert "commercial_score" in compact
    assert "sources:" not in compact
    assert "company_facts:" not in compact
    assert "economic_signals:" not in compact
    assert "agents:" not in compact
    assert "action_package:" not in compact
    assert "entries.map(compactHistoryEntry)" in script
    assert "localStorage.setItem(HIST_KEY, JSON.stringify(compact))" in script
    assert "/api/analyze/${encodeURIComponent(item.analysis_id)}" in script
    assert "status.result || status.partial_result" in script


def test_history_quota_failure_is_local_and_never_becomes_mission_failure() -> None:
    script = (STATIC / "workspace-legacy-result-compat.js").read_text(encoding="utf-8")
    assert "QuotaExceededError" in script
    assert "NS_ERROR_DOM_QUOTA_REACHED" in script
    assert "localStorage.removeItem(HIST_KEY)" in script
    assert "console.warn('AIMETON history persistence unavailable'" in script
    assert "throw error" not in script


def test_exports_and_chat_remain_owned_by_existing_app() -> None:
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/export/analysis.md" in app
    assert "/api/export/analysis.docx" in app
    assert "exportPDF" in app
    assert "saveToHistory" in app
    assert "renderChatSession" in app
