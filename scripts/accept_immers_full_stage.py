from __future__ import annotations

import collections
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


STAGE_URL = os.environ.get("STAGE_URL", "https://stage-auditor.aimeton.ru").rstrip("/")
TARGET_URL = os.environ.get("TARGET_URL", "https://aldenta.ru/")
USERNAME = os.environ["AIMETON_BOOTSTRAP_ADMIN_USERNAME"]
PASSWORD = os.environ["AIMETON_BOOTSTRAP_ADMIN_PASSWORD"]
EXPECTED_SHA = os.environ["EXPECTED_SHA"]
REPORT_PATH = Path(os.environ["REPORT_PATH"])


def _safe_event(name: str, payload: dict[str, Any]) -> None:
    print(json.dumps({"event": name, **payload}, ensure_ascii=False, sort_keys=True), flush=True)


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


def _immers_settings_snapshot(original: dict[str, Any]) -> dict[str, Any]:
    settings = json.loads(json.dumps(original))
    for role in ("fast_research", "extraction", "reasoning"):
        item = settings[role]
        item["profile_name"] = "immers-primary"
        item["model_id"] = None
        item["output_mode"] = "json_object"
        item["reasoning_mode"] = "inherit"
        item["reasoning_effort"] = None
        item["temperature"] = 0.0 if role == "fast_research" else 0.1
        item["timeout_seconds"] = 30 if role == "fast_research" else 180
    return settings


def _write_report(
    *,
    status: dict[str, Any],
    probe: dict[str, Any],
    restored: bool,
) -> None:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    facts = result.get("company_facts") if isinstance(result.get("company_facts"), list) else []
    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    research = status.get("research") if isinstance(status.get("research"), dict) else {}
    by_field = collections.Counter(
        str(item.get("field") or "") for item in facts if isinstance(item, dict)
    )
    summary = {
        "exact_deployed_sha": EXPECTED_SHA,
        "target": TARGET_URL,
        "terminal_state": status.get("state"),
        "phase": status.get("phase"),
        "analysis_id": status.get("analysis_id"),
        "mission_id": status.get("mission_id"),
        "provider": "immers",
        "profile": "immers-primary",
        "model": "deepseek-v4-flash-0731",
        "llm_settings_restored": restored,
        "admin_capability_probe": {
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
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "error_code",
            )
        },
        "company_name": result.get("company_name"),
        "company_fact_count": len(facts),
        "facts_by_field": dict(sorted(by_field.items())),
        "source_count": len(sources),
        "research_accounting": {
            key: research.get(key)
            for key in (
                "llm_calls",
                "prompt_tokens",
                "completion_tokens",
                "llm_usage_reports",
                "llm_usage_unknown",
                "completed_chunks",
                "llm_last_phase",
                "llm_last_provider",
                "llm_last_profile",
                "llm_last_model",
                "llm_last_error",
                "documents_attempted",
                "search_attempts",
                "identity_candidates_checked",
                "identity_resolution_state",
            )
            if key in research
        },
        "readiness": result.get("readiness"),
        "runtime_provider_evidence": {
            key: (result.get("research_status") or {}).get(key)
            for key in (
                "llm_extraction_provider", "llm_extraction_profile", "llm_extraction_model",
                "llm_reasoning_provider", "llm_reasoning_profile", "llm_reasoning_model",
                "commercial_reasoning_state", "commercial_reasoning_error_phase",
                "commercial_reasoning_failure",
            )
        },
    }
    REPORT_PATH.write_text(
        "## Immers full Site Audit acceptance\n\n"
        + f"- exact deployed SHA: {EXPECTED_SHA}\n"
        + f"- target: {TARGET_URL}\n"
        + f"- terminal state: {status.get('state', 'unknown')}\n"
        + "- temporary roles: fast_research, extraction, reasoning -> immers-primary\n"
        + f"- original LLM settings restored: {str(restored).lower()}\n\n"
        + "JSON evidence:\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)[:50000]
        + "\n\nSafety: no credentials, cookies, raw prompts, provider request bodies, "
        + "completion text, or chain-of-thought are published.\n",
        encoding="utf-8",
    )


def main() -> int:
    if not (len(EXPECTED_SHA) == 40 and all(ch in "0123456789abcdef" for ch in EXPECTED_SHA)):
        raise RuntimeError("invalid_expected_sha")

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
    original = snapshot["record"]["settings"]
    _safe_event("settings_snapshot", {"ok": True})

    candidate = _immers_settings_snapshot(original)
    probe = _json_request(
        opener,
        "POST",
        "/api/admin/llm-settings/test",
        csrf=csrf,
        payload={"role": "reasoning", "settings": candidate["reasoning"]},
        timeout=45,
    )
    _safe_event(
        "admin_capability_probe",
        {
            key: probe.get(key)
            for key in (
                "ok",
                "provider",
                "resolved_model",
                "latency_ms",
                "finish_reason",
                "structured_output_valid",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "error_code",
            )
        },
    )
    if not probe.get("ok") or probe.get("provider") != "immers":
        raise RuntimeError("immers_admin_capability_probe_failed")

    status: dict[str, Any] = {}
    restored = False
    switched = False
    run_error: BaseException | None = None
    try:
        switched_response = _json_request(
            opener,
            "PUT",
            "/api/admin/llm-settings",
            csrf=csrf,
            payload={
                "settings": candidate,
                "reason": "temporary Immers full Site Audit acceptance #1003",
            },
            timeout=30,
        )
        switched = True
        for role in ("fast_research", "extraction", "reasoning"):
            resolved = switched_response["resolved"][role]
            if not (
                resolved.get("provider") == "immers"
                and resolved.get("model") == "deepseek-v4-flash-0731"
                and resolved.get("configured") is True
            ):
                raise RuntimeError(f"immers_role_switch_failed:{role}")
        _safe_event("roles_switched", {"ok": True, "roles": 3})

        started = _json_request(
            opener,
            "POST",
            "/api/analyze/start",
            csrf=csrf,
            payload={
                "url": TARGET_URL,
                "deep_research": True,
                "unlimited_llm_budget": True,
            },
            timeout=60,
        )
        analysis_id = str(started["analysis_id"])
        mission_id = str(started["mission_id"])
        _safe_event(
            "audit_started",
            {"analysis_id": analysis_id, "mission_id": mission_id},
        )

        deadline = time.monotonic() + 45 * 60
        while time.monotonic() < deadline:
            status = _json_request(
                opener,
                "GET",
                f"/api/analyze/{analysis_id}",
                timeout=30,
            )
            state = str(status.get("state") or "")
            if state in {"completed", "failed"}:
                break
            time.sleep(3)
        else:
            raise RuntimeError("immers_full_audit_timeout")

        if status.get("state") != "completed":
            raise RuntimeError(f"immers_full_audit_{status.get('state') or 'unknown'}")
    except BaseException as exc:
        run_error = exc
    finally:
        if switched:
            try:
                restored_response = _json_request(
                    opener,
                    "PUT",
                    "/api/admin/llm-settings",
                    csrf=csrf,
                    payload={
                        "settings": original,
                        "reason": "restore settings after Immers full Site Audit acceptance #1003",
                    },
                    timeout=30,
                )
                restored = restored_response["record"]["settings"] == original
                _safe_event("settings_restore", {"ok": restored})
            except BaseException as exc:
                _safe_event("settings_restore", {"ok": False, "error_class": type(exc).__name__})
                if run_error is None:
                    run_error = exc

    _write_report(status=status, probe=probe, restored=restored)
    if run_error is not None:
        raise run_error
    if not restored:
        raise RuntimeError("llm_settings_restore_failed")
    _safe_event(
        "immers_full_audit",
        {
            "ok": True,
            "state": status.get("state"),
            "analysis_id": status.get("analysis_id"),
            "mission_id": status.get("mission_id"),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        _safe_event("acceptance_failed", {"error_class": type(exc).__name__, "error": str(exc)[:240]})
        raise
