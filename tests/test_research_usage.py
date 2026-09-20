import httpx
import pytest
from pydantic import BaseModel

from app.heuristics import heuristic_analysis
from app.llm import analyze_with_routerai, chat_with_routerai
from app.research_control import (
    ResearchControl,
    ResearchStopped,
    bind_research,
    record_identity_resolution_progress,
    record_llm_start,
    record_llm_usage,
)
from app.routerai_split_synthesis import _request_json
from app.routerai_strict_request import request_json_strict


class Answer(BaseModel):
    answer: str


async def invoke(path):
    if path == "analysis":
        return await analyze_with_routerai("https://example.org", "Example", "Content")
    if path == "chat":
        return await chat_with_routerai(heuristic_analysis("https://example.org", "Example", "Content"), [])
    function = _request_json if path == "split" else request_json_strict
    return await function("test", Answer, system="system", prompt="prompt", max_tokens=100, timeout_seconds=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["analysis", "chat", "split", "strict"])
async def test_usage_is_accounted_before_output_validation(path, monkeypatch, tmp_path):
    monkeypatch.setenv("ROUTERAI_API_KEY", "test")
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    client = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 17, "completion_tokens": 3},
            "choices": [{"message": {"content": "malformed JSON"}}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    control = ResearchControl()
    with bind_research(control):
        if path == "chat":
            assert await invoke(path) == "malformed JSON"
        else:
            with pytest.raises((ValueError, RuntimeError)):
                await invoke(path)
    assert len(requests) == control.llm_calls == control.llm_usage_reports == 1
    assert (control.prompt_tokens, control.completion_tokens) == (17, 3)
    assert control.snapshot()["llm_usage_unknown"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["analysis", "chat", "split", "strict"])
async def test_stopped_run_never_sends_provider_request(path, monkeypatch):
    monkeypatch.setenv("ROUTERAI_API_KEY", "test")
    def unexpected_client(**kwargs):
        pytest.fail("stopped run attempted provider I/O")
    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    control = ResearchControl(stop_requested=True)
    with bind_research(control), pytest.raises(ResearchStopped):
        await invoke(path)
    assert control.llm_calls == 0


@pytest.mark.parametrize("body", [{}, {"usage": None}, {"usage": "bad"},
    {"usage": {"prompt_tokens": True, "completion_tokens": -1}},
    {"usage": {"prompt_tokens": "10", "completion_tokens": 3}}])
def test_missing_or_malformed_usage_stays_unknown(body, monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    control = ResearchControl()
    with bind_research(control):
        record_llm_start()
        record_llm_usage(body)
    assert control.snapshot()["llm_usage_unknown"] == 1
    assert control.llm_usage_reports == 0



def test_identity_resolution_progress_is_exposed_in_safe_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    control = ResearchControl()
    with bind_research(control):
        record_identity_resolution_progress(
            candidates_checked=2,
            resolution_state="registry_mirror_verified",
            selected_inn="2462215501",
            selected_ogrn="1112468013030",
        )
    snapshot = control.snapshot()
    assert snapshot["identity_candidates_checked"] == 2
    assert snapshot["identity_resolution_state"] == "registry_mirror_verified"
    assert snapshot["identity_selected"] is True
    assert snapshot["identity_selected_inn"] == "2462215501"
    assert snapshot["identity_selected_ogrn"] == "1112468013030"
