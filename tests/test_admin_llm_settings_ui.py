from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_workspace_exposes_llm_control_center() -> None:
    html = (ROOT / "static" / "admin-workspace.html").read_text(encoding="utf-8")
    assert 'id="llm-settings-title"' in html
    assert 'id="llm-role"' in html
    assert 'id="llm-profile"' in html
    assert 'id="llm-model-id"' in html
    assert 'id="llm-temperature"' in html
    assert 'id="llm-max-tokens"' in html
    assert 'id="llm-timeout-seconds"' in html
    assert 'id="llm-output-mode"' in html
    assert 'id="llm-reasoning-mode"' in html
    assert 'id="llm-reasoning-effort"' in html
    assert 'id="test-llm-model"' in html
    assert "/static/admin-llm-settings.js" in html
    assert "API-ключи здесь не отображаются" in html


def test_admin_llm_client_uses_admin_api_and_csrf_without_secret_fields() -> None:
    source = (ROOT / "static" / "admin-llm-settings.js").read_text(encoding="utf-8")
    assert "/api/admin/llm-settings" in source
    assert "/api/admin/llm-settings/test" in source
    assert "X-CSRF-Token" in source
    assert "profile_name" in source
    assert "model_id" in source
    assert "reasoning_mode" in source
    assert "api_key" not in source
    assert "ROUTERAI_API_KEY" not in source
