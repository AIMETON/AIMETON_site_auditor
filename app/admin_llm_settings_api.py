from __future__ import annotations

import asyncio
import hmac
import json
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import User
from app.auth_api import CSRF_COOKIE, CSRF_HEADER, require_admin
from app.llm_runtime_settings import (
    LlmReasoningMode,
    LlmRole,
    LlmRoleSettings,
    LlmRuntimeSettings,
    LlmRuntimeSettingsRecord,
    get_llm_runtime_settings_repository,
    resolve_llm_runtime,
)
from app.search_observer_models import OBSERVER_MODEL_PROFILES


router = APIRouter(prefix="/api/admin/llm-settings", tags=["admin-llm-settings"])


class AdminLlmSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: LlmRuntimeSettings
    reason: str = Field(min_length=1, max_length=500)


class AdminLlmProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: LlmRole
    settings: LlmRoleSettings


class _ProbeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    message: str = Field(max_length=120)


class AdminLlmProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    role: LlmRole
    profile_name: str
    provider: str
    resolved_model: str
    latency_ms: int | None = None
    finish_reason: str | None = None
    structured_output_valid: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None


def _require_csrf(cookie_token: str | None, header_token: str | None) -> None:
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail={"reason": "csrf_failed"})


def _runtime_profiles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in OBSERVER_MODEL_PROFILES:
        if profile.provider.value != "routerai":
            continue
        resolved = profile.resolve()
        rows.append({
            "profile_name": profile.name,
            "provider": profile.provider.value,
            "model": resolved.model,
            "tier": profile.tier,
            "configured": resolved.configured,
        })
    return rows


def _envelope(record: LlmRuntimeSettingsRecord) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for role in LlmRole:
        try:
            resolved[role.value] = resolve_llm_runtime(
                role, settings=record.settings
            ).safe_descriptor()
        except Exception:
            resolved[role.value] = {
                "role": role.value,
                "configured": False,
                "error": "profile_resolution_failed",
            }
    return {
        "record": record.model_dump(mode="json"),
        "profiles": _runtime_profiles(),
        "resolved": resolved,
    }


@router.get("")
def read_llm_settings(_admin: User = Depends(require_admin)) -> dict[str, Any]:
    return _envelope(get_llm_runtime_settings_repository().get())


@router.put("")
def update_llm_settings(
    payload: AdminLlmSettingsUpdate,
    admin: User = Depends(require_admin),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> dict[str, Any]:
    _require_csrf(csrf_cookie, csrf_header)
    try:
        record = get_llm_runtime_settings_repository().save(
            payload.settings,
            actor_id=admin.id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc
    return _envelope(record)


@router.post("/test", response_model=AdminLlmProbeResult)
async def test_llm_settings(
    payload: AdminLlmProbeRequest,
    _admin: User = Depends(require_admin),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> AdminLlmProbeResult:
    _require_csrf(csrf_cookie, csrf_header)
    candidate = LlmRuntimeSettings()
    candidate = candidate.model_copy(update={payload.role.value: payload.settings})
    try:
        runtime = resolve_llm_runtime(payload.role, settings=candidate)
    except Exception as exc:
        return AdminLlmProbeResult(
            ok=False,
            role=payload.role,
            profile_name=payload.settings.profile_name,
            provider="routerai",
            resolved_model=payload.settings.model_id or "",
            error_code=type(exc).__name__,
        )

    if not runtime.configured:
        return AdminLlmProbeResult(
            ok=False,
            role=payload.role,
            profile_name=runtime.profile_name,
            provider=runtime.provider,
            resolved_model=runtime.model,
            error_code="model_profile_not_configured",
        )

    schema = _ProbeSchema.model_json_schema()
    response_format: dict[str, Any]
    if runtime.output_mode.value == "strict_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "aimeton_admin_llm_probe",
                "strict": True,
                "schema": schema,
            },
        }
    else:
        response_format = {"type": "json_object"}

    request_json: dict[str, Any] = {
        "model": runtime.model,
        "temperature": runtime.temperature,
        "max_tokens": min(runtime.max_tokens, 256),
        "response_format": response_format,
        "messages": [
            {
                "role": "system",
                "content": "Return only JSON. This is an AIMETON admin model capability probe.",
            },
            {
                "role": "user",
                "content": (
                    'Return {"ok": true, "message": "ready"} and no additional facts.'
                ),
            },
        ],
    }
    if runtime.output_mode.value == "strict_schema":
        request_json["structured_outputs"] = True
    if runtime.reasoning_mode is LlmReasoningMode.ON:
        reasoning: dict[str, Any] = {"enabled": True}
        if runtime.reasoning_effort is not None:
            reasoning["effort"] = runtime.reasoning_effort.value
        request_json["reasoning"] = reasoning
    elif runtime.reasoning_mode is LlmReasoningMode.OFF:
        request_json["reasoning"] = {"enabled": False}
    elif runtime.reasoning_effort is not None:
        request_json["reasoning"] = {"effort": runtime.reasoning_effort.value}

    started = perf_counter()
    try:
        timeout = min(runtime.timeout_seconds, 30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{runtime.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {runtime.api_key}"},
                json=request_json,
            )
            response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        content = choice["message"]["content"]
        parsed = _ProbeSchema.model_validate(json.loads(content))
        usage = body.get("usage") or {}
        return AdminLlmProbeResult(
            ok=bool(parsed.ok),
            role=payload.role,
            profile_name=runtime.profile_name,
            provider=runtime.provider,
            resolved_model=str(body.get("model") or runtime.model),
            latency_ms=round((perf_counter() - started) * 1000),
            finish_reason=choice.get("finish_reason"),
            structured_output_valid=True,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        error_code = "timeout"
    except httpx.HTTPStatusError as exc:
        error_code = f"http_{exc.response.status_code}"
    except Exception as exc:
        error_code = type(exc).__name__

    return AdminLlmProbeResult(
        ok=False,
        role=payload.role,
        profile_name=runtime.profile_name,
        provider=runtime.provider,
        resolved_model=runtime.model,
        latency_ms=round((perf_counter() - started) * 1000),
        error_code=error_code,
    )
