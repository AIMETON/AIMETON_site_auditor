from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import openrouter_proxy_client as client


def test_request_empty_proxy_remains_fail_closed_without_explicit_direct() -> None:
    with pytest.raises(RuntimeError, match="proxy_url_missing_or_invalid"):
        client.request(
            method="GET",
            url="https://example.invalid/",
            proxy="",
        )


def test_request_explicit_direct_omits_curl_proxy_argument(monkeypatch) -> None:
    observed: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        observed["command"] = list(command)
        output_path = Path(command[command.index("--output") + 1])
        header_path = Path(command[command.index("--dump-header") + 1])
        output_path.write_bytes(b'{"error":"invalid"}')
        header_path.write_text("HTTP/1.1 400 Bad Request\r\n\r\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b"AIMETON_CURL_METRICS|400|0|0.01|0.02|0.03|0.04",
            stderr=b"",
        )

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    result = client.request(
        method="GET",
        url="https://example.invalid/",
        proxy="",
        allow_direct=True,
    )
    assert result.http_status == 400
    assert "--proxy" not in observed["command"]


def test_authenticated_direct_still_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="openrouter_api_key_missing"):
        client.authenticated_get(
            url=client.OPENROUTER_KEY_URL,
            proxy="",
            api_key="",
            allow_direct=True,
        )
