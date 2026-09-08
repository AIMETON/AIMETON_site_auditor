from pathlib import Path

EDGE = Path(".github/workflows/accb-openrouter-edge-large-body-probe.yml")
PREFLIGHT = Path(".github/workflows/accb-openrouter-proxy-large-body-preflight.yml")


def test_accb_edge_probe_is_dispatch_only_exact_size_and_zero_spend() -> None:
    text = EDGE.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]

    assert "workflow_dispatch:" in trigger
    assert "issue_comment:" not in trigger
    assert "inputs.expected_sha" in text
    assert "inputs.proxy_mode" in text
    assert "OPENROUTER_PROXY_URL" in text
    assert "OPENROUTER_SOCKS_URL" in text
    assert "TARGET_REQUEST_BYTES = 89470" in text
    assert '"model": {"invalid": True}' in text
    assert '"model_generation_requests": 0' in text
    assert '"paid_spend_authorized_rub": 0' in text
    assert '"allow_fallbacks": False' in text
    assert '"store": False' in text
    assert '"automatic_retries": 0' in text
    assert 'total_timeout_seconds=90' in text
    assert 'assert receipt["request_body_bytes"] == 89470' in text
    assert 'assert receipt["key_usage_unchanged"] is True' in text
    assert 'assert receipt.get("edge_response_observed") is True' in text
    assert '400 <= int(receipt["http_status"]) < 500' in text
    assert 'proxy_mode == "direct"' in text
    assert 'proxy_mode == "http"' in text
    assert 'proxy_mode == "socks"' in text
    assert "runs-on: ubuntu-24.04" in text
    assert "owner_spend_authorized" not in text
    assert "max_budget_rub" not in text


def test_large_body_preflight_enforces_current_socks_transport() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert '"proxy_transport": "socks5h"' in text
    assert 'assert receipt["proxy_transport"] == "socks5h"' in text
    assert 'assert receipt["proxy_transport"] == "http"' not in text


def test_openrouter_client_has_explicit_no_proxy_direct_transport() -> None:
    client = Path("scripts/openrouter_proxy_client.py").read_text(encoding="utf-8")
    assert 'if proxy:' in client
    assert 'command.extend(["--proxy", proxy])' in client
    assert 'command.extend(["--noproxy", "*"])' in client
    assert 'elif not api_key.strip():' in client
