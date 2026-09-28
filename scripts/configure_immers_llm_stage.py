from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.error
import urllib.request
from typing import Any


STAGE_URL = os.environ.get("STAGE_URL", "https://stage-auditor.aimeton.ru").rstrip("/")
USERNAME = os.environ["AIMETON_BOOTSTRAP_ADMIN_USERNAME"]
PASSWORD = os.environ["AIMETON_BOOTSTRAP_ADMIN_PASSWORD"]


def _opener() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def _csrf(jar: http.cookiejar.CookieJar) -> str:
    for cookie in jar:
        if cookie.name == "aimeton_csrf":
            return cookie.value
    raise RuntimeError("csrf_cookie_missing")


def _json_request(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    csrf: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        STAGE_URL + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"http_{exc.code}:{method}:{path}") from exc


def _candidate(original: dict[str, Any]) -> dict[str, Any]:
    settings = json.loads(json.dumps(original))
    role_models = {
        "fast_research": "qwen3.6-35b-a3b",
        "extraction": "deepseek-v4-flash-0731",
        "reasoning": "deepseek-v4-flash-0731",
    }
    for role, model_id in role_models.items():
        item = settings[role]
        item["profile_name"] = "immers-primary"
        item["model_id"] = model_id
        item["output_mode"] = "inherit"
        item["reasoning_mode"] = "off" if role == "fast_research" else "inherit"
        item["reasoning_effort"] = None
        item["temperature"] = 0.0 if role == "fast_research" else 0.1
        item["timeout_seconds"] = 30 if role == "fast_research" else 180
    return settings


def _safe_probe(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        key: probe.get(key)
        for key in (
            "ok",
            "role",
            "profile_name",
            "provider",
            "resolved_model",
            "latency_ms",
            "finish_reason",
            "structured_output_valid",
            "effective_output_mode",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "error_code",
        )
    }


def main() -> int:
    opener, jar = _opener()
    _json_request(
        opener,
        "POST",
        "/api/auth/login",
        payload={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    csrf = _csrf(jar)
    snapshot = _json_request(opener, "GET", "/api/admin/llm-settings", timeout=30)
    candidate = _candidate(snapshot["record"]["settings"])

    probes: dict[str, dict[str, Any]] = {}
    for role in ("fast_research", "extraction"):
        probe = _json_request(
            opener,
            "POST",
            "/api/admin/llm-settings/test",
            csrf=csrf,
            payload={"role": role, "settings": candidate[role]},
            timeout=60,
        )
        probes[role] = _safe_probe(probe)
        if not probe.get("ok") or probe.get("provider") != "immers":
            raise RuntimeError(f"immers_probe_failed:{role}")
        expected_model = (
            "qwen3.6-35b-a3b"
            if role == "fast_research"
            else "deepseek-v4-flash-0731"
        )
        if probe.get("resolved_model") != expected_model:
            raise RuntimeError(f"immers_probe_wrong_model:{role}")

    saved = _json_request(
        opener,
        "PUT",
        "/api/admin/llm-settings",
        csrf=csrf,
        payload={
            "settings": candidate,
            "reason": "owner-selected Immers role routing: fast Qwen, heavy DeepSeek",
        },
        timeout=30,
    )
    readback = _json_request(opener, "GET", "/api/admin/llm-settings", timeout=30)

    expected = {
        "fast_research": "qwen3.6-35b-a3b",
        "extraction": "deepseek-v4-flash-0731",
        "reasoning": "deepseek-v4-flash-0731",
    }
    for role, model in expected.items():
        resolved = readback["resolved"][role]
        if not (
            resolved.get("provider") == "immers"
            and resolved.get("profile_name") == "immers-primary"
            and resolved.get("model") == model
            and resolved.get("configured") is True
        ):
            raise RuntimeError(f"immers_readback_failed:{role}")

    print(json.dumps({
        "ok": True,
        "saved_reason": (saved.get("record") or {}).get("reason"),
        "resolved": {
            role: {
                "provider": readback["resolved"][role].get("provider"),
                "profile_name": readback["resolved"][role].get("profile_name"),
                "model": readback["resolved"][role].get("model"),
                "configured": readback["resolved"][role].get("configured"),
                "effective_output_mode": readback["resolved"][role].get("effective_output_mode"),
            }
            for role in expected
        },
        "probes": probes,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
