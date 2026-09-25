from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

import app.routerai_strict_request as strict
from app.routerai_profile_extraction import ManagementSlice
from app.routerai_split_synthesis import SplitSynthesisPhaseError


def _management_content() -> str:
    return json.dumps(
        {
            "company_facts": [
                {
                    "field": "executives",
                    "value": "Named executive",
                    "period": None,
                    "confidence": "Средняя",
                    "source_ids": ["S1"],
                }
            ],
            "risks_and_assumptions": [],
        },
        ensure_ascii=False,
    )


def _install_fake_client(monkeypatch, captured: dict) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _management_content()},
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only")
    monkeypatch.setattr(strict.httpx, "AsyncClient", lambda timeout: FakeClient())


def test_strict_request_uses_provider_json_schema_without_reasoning_override(monkeypatch) -> None:
    captured: dict = {}
    _install_fake_client(monkeypatch, captured)

    result = asyncio.run(
        strict.request_json_strict(
            "profile_management",
            ManagementSlice,
            system="Structured only",
            prompt="Extract management",
            max_tokens=600,
            timeout_seconds=1,
        )
    )

    payload = captured["payload"]
    assert payload["structured_outputs"] is True
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["name"] == "profile_management"
    assert payload["response_format"]["json_schema"]["schema"] == ManagementSlice.model_json_schema()
    assert "reasoning" not in payload
    assert result.company_facts[0].field == "executives"


def test_strict_request_can_disable_reasoning_for_deterministic_extraction(monkeypatch) -> None:
    captured: dict = {}
    _install_fake_client(monkeypatch, captured)

    asyncio.run(
        strict.request_json_strict(
            "profile_management",
            ManagementSlice,
            system="Structured only",
            prompt="Extract management",
            max_tokens=900,
            timeout_seconds=18.0,
            reasoning_enabled=False,
        )
    )

    payload = captured["payload"]
    assert payload["max_tokens"] == 900
    assert payload["reasoning"] == {"enabled": False}


def test_strict_request_can_bound_reasoning_effort_without_disabling_reasoning(monkeypatch) -> None:
    captured: dict = {}
    _install_fake_client(monkeypatch, captured)

    asyncio.run(
        strict.request_json_strict(
            "commercial_reasoning",
            ManagementSlice,
            system="Structured only",
            prompt="Choose opportunity",
            max_tokens=1500,
            timeout_seconds=25.0,
            reasoning_effort="high",
        )
    )

    payload = captured["payload"]
    assert payload["max_tokens"] == 1500
    assert payload["reasoning"] == {"effort": "high"}
    assert "enabled" not in payload["reasoning"]


def test_strict_request_surfaces_output_truncation(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "{}"},
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            return FakeResponse()

    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only")
    monkeypatch.setattr(strict.httpx, "AsyncClient", lambda timeout: FakeClient())

    with pytest.raises(SplitSynthesisPhaseError) as exc_info:
        asyncio.run(
            strict.request_json_strict(
                "profile_management",
                ManagementSlice,
                system="Structured only",
                prompt="Extract management",
                max_tokens=600,
                timeout_seconds=1,
            )
        )

    assert exc_info.value.phase == "profile_management"
    assert exc_info.value.error_type == "OutputTruncated"


class _RepairShape(BaseModel):
    opportunity_type: str
    score: int


def test_strict_request_repairs_schema_drift_once_without_reanalysis(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, content: str):
            self._content = content

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": self._content},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            calls.append(json)
            if len(calls) == 1:
                return FakeResponse('{"score":72}')
            return FakeResponse('{"opportunity_type":"Недостаточно данных","score":72}')

    monkeypatch.setenv("ROUTERAI_API_KEY", "test-only")
    monkeypatch.setattr(strict.httpx, "AsyncClient", lambda timeout: FakeClient())

    result = asyncio.run(
        strict.request_json_strict(
            "compiled_business_commercial_synthesis",
            _RepairShape,
            system="Structured only",
            prompt="Build result",
            max_tokens=500,
            timeout_seconds=5,
        )
    )

    assert result.score == 72
    assert result.opportunity_type == "Недостаточно данных"
    assert len(calls) == 2
    assert "VALIDATION ERRORS" in calls[1]["messages"][1]["content"]
    assert "PREVIOUS JSON OUTPUT" in calls[1]["messages"][1]["content"]
    assert calls[1]["reasoning"] == {"enabled": False}
