from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping


OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

ACCB_COMPACT_FILLER_CORPUS_VERSION = "accb-layer-b-transport-compact-v0.2"
ACCB_COMPACT_FILLER_MAP = {
    "legacyfallback": "old",
    "cachedpolicy": "old",
    "autofallback": "old",
    "stalehandoff": "old",
    "oldrouting": "old",
    "obsoletebudget": "old",
    "deprecateddecision": "old",
    "telemetry": "n",
    "checkpoint": "n",
    "inventory": "n",
    "heartbeat": "n",
    "observability": "n",
    "scheduler": "n",
    "archive": "n",
    "baseline": "n",
    "diagnostic": "n",
    "metadata": "n",
    "capacity": "n",
    "latency": "n",
    "checksum": "n",
}


@dataclass(frozen=True)
class CurlResult:
    http_status: int
    body: bytes
    metrics: dict[str, Any]
    diagnostic_headers: dict[str, str]


class CurlTransportError(RuntimeError):
    def __init__(
        self,
        failure_class: str,
        metrics: Mapping[str, Any],
        partial_body: bytes = b"",
    ):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.metrics = dict(metrics)
        self.partial_body = partial_body


def validate_proxy_and_key(proxy: str, api_key: str | None = None) -> None:
    if not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
        raise RuntimeError("proxy_url_missing_or_invalid")
    if api_key is not None and not api_key.strip():
        raise RuntimeError("openrouter_api_key_missing")


def resolve_proxy(http_proxy: str, socks5_proxy: str = "", *, socks5_port: int = 50101) -> str:
    if socks5_proxy.strip():
        resolved = socks5_proxy.strip()
        validate_proxy_and_key(resolved)
        if not resolved.startswith(("socks5://", "socks5h://")):
            raise RuntimeError("openrouter_socks5_url_invalid")
        return resolved
    validate_proxy_and_key(http_proxy)
    parsed = urllib.parse.urlsplit(http_proxy)
    if not parsed.hostname:
        raise RuntimeError("proxy_hostname_missing")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"socks5h://{host}:{socks5_port}"


def compact_accb_context(context: str) -> tuple[str, dict[str, Any]]:
    source_tokens = context.split(" ")
    wire_tokens: list[str] = []
    replaced = 0
    for token in source_tokens:
        stem = token[:-3] if len(token) > 3 and token[-3:].isdigit() else ""
        replacement = ACCB_COMPACT_FILLER_MAP.get(stem)
        if replacement is None:
            wire_tokens.append(token)
            continue
        wire_tokens.append(replacement)
        replaced += 1
    if len(wire_tokens) != len(source_tokens):
        raise RuntimeError("accb_compact_logical_token_count_changed")
    wire_context = " ".join(wire_tokens)
    return wire_context, {
        "wire_filler_corpus_version": ACCB_COMPACT_FILLER_CORPUS_VERSION,
        "logical_whitespace_tokens": len(wire_tokens),
        "replaced_filler_tokens": replaced,
        "source_context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "wire_context_sha256": hashlib.sha256(wire_context.encode("utf-8")).hexdigest(),
        "wire_context_bytes": len(wire_context.encode("utf-8")),
    }


def build_response_body(
    input_text: str,
    *,
    max_output_tokens: int,
    stream: bool = False,
) -> bytes:
    payload = {
        "model": "openai/gpt-5.6-sol",
        "input": input_text,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": "low"},
        "store": False,
        "provider": {
            "only": ["openai"],
            "order": ["openai"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        },
    }
    if stream:
        payload["stream"] = True
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_response_stream(body: bytes) -> dict[str, Any]:
    output_parts: list[str] = []
    event_types: list[str] = []
    terminal_response: dict[str, Any] = {}
    response_id: str | None = None
    generation_id: str | None = None
    for raw_line in body.decode("utf-8", errors="replace").splitlines():
        if not raw_line.startswith("data: "):
            continue
        raw_data = raw_line[6:]
        if raw_data == "[DONE]":
            event_types.append("done")
            continue
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "unknown")[:80]
        event_types.append(event_type)
        if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            output_parts.append(str(event["delta"]))
        response = event.get("response")
        if isinstance(response, dict):
            if response.get("id"):
                response_id = str(response["id"])[:128]
            if event_type in {"response.completed", "response.failed", "response.incomplete"}:
                terminal_response = response
        generation_id = generation_id or find_generation_id(event)
    return {
        "output_text": "".join(output_parts).strip(),
        "event_types": event_types,
        "terminal_response": terminal_response,
        "response_id": response_id,
        "generation_id": generation_id,
    }


def extract_output_text(data: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(data.get("output_text"), str):
        parts.append(str(data["output_text"]))
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(str(part["text"]))
    return "\n".join(parts).strip()


def find_generation_id(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"gen-[A-Za-z0-9_-]+", value):
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = find_generation_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_generation_id(child)
            if found:
                return found
    return None


def request(
    *,
    method: str,
    url: str,
    proxy: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    connect_timeout_seconds: int = 30,
    total_timeout_seconds: int = 240,
) -> CurlResult:
    if proxy:
        validate_proxy_and_key(proxy)
    request_headers = dict(headers or {})
    expected_upload = len(body or b"")
    write_out = "AIMETON_CURL_METRICS|%{http_code}|%{size_upload}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}"

    with tempfile.TemporaryDirectory(prefix="aimeton-openrouter-") as temp_dir:
        root = pathlib.Path(temp_dir)
        header_path = root / "request-headers.txt"
        response_header_path = root / "response-headers.txt"
        response_path = root / "response.bin"
        header_path.write_text(
            "".join(f"{name}: {value}\n" for name, value in request_headers.items()),
            encoding="utf-8",
        )
        os.chmod(header_path, 0o600)

        command = [
            "curl",
            "--http1.1",
            "--silent",
            "--show-error",
            "--no-progress-meter",
            "--request",
            method,
        ]
        if proxy:
            command.extend(["--proxy", proxy])
        else:
            command.extend(["--noproxy", "*"])
        command.extend([
            "--connect-timeout",
            str(connect_timeout_seconds),
            "--max-time",
            str(total_timeout_seconds),
            "--retry",
            "0",
            "--header",
            f"@{header_path}",
            "--dump-header",
            str(response_header_path),
            "--output",
            str(response_path),
            "--write-out",
            write_out,
        ])
        if body is not None:
            body_path = root / "request.bin"
            body_path.write_bytes(body)
            os.chmod(body_path, 0o600)
            command.extend(["--data-binary", f"@{body_path}"])
        command.append(url)

        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=total_timeout_seconds + 15,
            )
        except FileNotFoundError as exc:
            raise CurlTransportError("curl_not_installed", {"request_body_bytes": expected_upload}) from exc
        except subprocess.TimeoutExpired as exc:
            raise CurlTransportError("curl_process_timeout", {"request_body_bytes": expected_upload}) from exc

        metrics = _parse_metrics(completed.stdout.decode("utf-8", errors="replace"), expected_upload)
        metrics["curl_exit_code"] = int(completed.returncode)
        metrics["upload_complete"] = metrics.get("uploaded_bytes") == expected_upload
        response_body = response_path.read_bytes() if response_path.exists() else b""
        if completed.returncode != 0:
            raise CurlTransportError(
                _classify_curl_failure(completed.returncode, metrics),
                metrics,
                partial_body=response_body,
            )

        diagnostic_headers = _read_diagnostic_headers(response_header_path)
        return CurlResult(
            http_status=int(metrics.get("http_status") or 0),
            body=response_body,
            metrics=metrics,
            diagnostic_headers=diagnostic_headers,
        )


def authenticated_get(*, url: str, proxy: str, api_key: str, total_timeout_seconds: int = 90) -> CurlResult:
    if proxy:
        validate_proxy_and_key(proxy, api_key)
    elif not api_key.strip():
        raise RuntimeError("openrouter_api_key_missing")
    return request(
        method="GET",
        url=url,
        proxy=proxy,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "aimeton-accb-openrouter-client",
        },
        total_timeout_seconds=total_timeout_seconds,
    )


def post_response(*, proxy: str, api_key: str, body: bytes, total_timeout_seconds: int) -> CurlResult:
    if proxy:
        validate_proxy_and_key(proxy, api_key)
    elif not api_key.strip():
        raise RuntimeError("openrouter_api_key_missing")
    return request(
        method="POST",
        url=OPENROUTER_RESPONSES_URL,
        proxy=proxy,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "aimeton-accb-openrouter-client",
            "X-OpenRouter-Metadata": "enabled",
        },
        body=body,
        connect_timeout_seconds=30,
        total_timeout_seconds=total_timeout_seconds,
    )


def _parse_metrics(raw: str, request_body_bytes: int) -> dict[str, Any]:
    metrics: dict[str, Any] = {"request_body_bytes": request_body_bytes}
    marker = "AIMETON_CURL_METRICS|"
    line = next((part for part in raw.splitlines() if part.startswith(marker)), "")
    fields = line[len(marker):].split("|") if line else []
    if len(fields) != 6:
        return metrics
    try:
        metrics.update(
            {
                "http_status": int(fields[0]),
                "uploaded_bytes": int(float(fields[1])),
                "time_connect_seconds": float(fields[2]),
                "time_tls_seconds": float(fields[3]),
                "time_first_byte_seconds": float(fields[4]),
                "time_total_seconds": float(fields[5]),
            }
        )
    except ValueError:
        metrics["metrics_parse_failed"] = True
    return metrics


def _classify_curl_failure(exit_code: int, metrics: Mapping[str, Any]) -> str:
    if exit_code == 28:
        uploaded = metrics.get("uploaded_bytes")
        expected = metrics.get("request_body_bytes")
        if isinstance(uploaded, int) and isinstance(expected, int) and uploaded < expected:
            return "upload_timeout"
        return "response_timeout_after_upload"
    return {
        5: "proxy_name_resolution_failed",
        6: "destination_name_resolution_failed",
        7: "proxy_or_destination_connect_failed",
        35: "tls_handshake_failed",
        52: "empty_response",
        55: "upload_failed",
        56: "response_receive_failed",
        92: "http2_stream_error",
    }.get(exit_code, f"curl_exit_{exit_code}")


def _read_diagnostic_headers(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    allowed = {"x-request-id", "cf-ray"}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="iso-8859-1", errors="replace").splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized = name.strip().lower()
        if normalized in allowed or normalized.startswith("x-openrouter-"):
            result[normalized] = value.strip()[:256]
    return result
