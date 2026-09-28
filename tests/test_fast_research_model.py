from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import BaseModel

import app.fast_research_model as fast
from app.llm_runtime_settings import (
    LlmOutputMode,
    LlmReasoningMode,
    LlmRole,
    ResolvedLlmRuntime,
)


class _Decision(BaseModel):
    decision: str


def _runtime() -> ResolvedLlmRuntime:
    return ResolvedLlmRuntime(
        role=LlmRole.FAST_RESEARCH,
        profile_name="routerai-qwen35-9b",
        provider="routerai",
        base_url="https://router.example/v1",
        api_key="test-key",
        model="qwen/qwen3.5-9b",
        configured=True,
        temperature=0.0,
        max_tokens=1200,
        timeout_seconds=15,
        output_mode=LlmOutputMode.INHERIT,
        reasoning_mode=LlmReasoningMode.OFF,
        reasoning_effort=None,
        structured_output_supported=None,
        json_mode_supported=None,
    )


def test_fast_inherit_uses_json_object_when_strict_support_unknown(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"decision": "include"})},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers, json):
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(fast, "resolve_fast_research_model", _runtime)
    monkeypatch.setattr(fast.httpx, "AsyncClient", lambda timeout: Client())

    result = asyncio.run(
        fast.request_fast_json(
            "fast_probe",
            _Decision,
            system="classify",
            prompt="input",
            max_tokens=200,
            timeout_seconds=5,
        )
    )

    assert result.decision == "include"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert "structured_outputs" not in captured["payload"]


def test_fast_read_retries_remote_protocol_error_then_succeeds(monkeypatch) -> None:
    calls = []
    sleeps = []
    starts = []
    failures = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"decision": "include"})},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers, json):
            calls.append(json)
            if len(calls) == 1:
                raise httpx.RemoteProtocolError("server disconnected")
            return Response()

    async def fake_sleep(value):
        sleeps.append(value)

    monkeypatch.setattr(fast, "resolve_fast_research_model", _runtime)
    monkeypatch.setattr(fast.httpx, "AsyncClient", lambda timeout: Client())
    monkeypatch.setattr(fast.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(fast.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(fast, "record_llm_start", lambda **kwargs: starts.append(kwargs))
    monkeypatch.setattr(fast, "record_llm_failure", lambda **kwargs: failures.append(kwargs))

    result = asyncio.run(
        fast.request_fast_json(
            "fast_probe",
            _Decision,
            system="classify",
            prompt="input",
            max_tokens=200,
            timeout_seconds=5,
        )
    )

    assert result.decision == "include"
    assert len(calls) == 2
    assert len(starts) == 2
    assert failures[0]["error_type"] == "RemoteProtocolError_retrying"
    assert sleeps == [2.0]
