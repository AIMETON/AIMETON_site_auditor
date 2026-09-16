import asyncio
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

import app.analysis_async_api as api
from app.models import AnalyzeRequest
from app.research_control import (
    CONTROLS, ResearchControl, bind_analysis_control, bind_research,
    recovered_research_snapshot, record_llm_start, record_llm_usage, record_search_attempt,
)
from app.research_settings import ResearchSettings, SettingsConflict


@pytest.mark.parametrize("configured", [False, True])
def test_launch_binding_recovers_usage_after_process_state_is_lost(tmp_path, monkeypatch, configured):
    path = tmp_path / "runtime.db"
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(path))
    monkeypatch.setenv("AIMETON_TRACE_DB", str(path))
    monkeypatch.setattr(api, "runtime_instance_id", lambda: "old-process")
    control = ResearchControl(owner_id=1, deep=True,
        settings=ResearchSettings() if configured else None,
        settings_revision=0 if configured else None)
    monkeypatch.setattr(api, "authorize_research", lambda *args, **kwargs: control)
    background = BackgroundTasks()
    started = asyncio.run(api.start_analysis(AnalyzeRequest(url="https://example.org"), background))
    assert len(background.tasks) == 1
    with bind_research(control):
        record_llm_start()
        record_llm_usage({"usage": {"prompt_tokens": 17, "completion_tokens": 3}})
        record_search_attempt(SimpleNamespace(cost_amount=Decimal("0.125"), cost_currency="RUB", paid=True))
        record_llm_start()  # response never arrives; this call must remain unknown
    expected = control.snapshot()
    api._ANALYSES.pop(started.analysis_id)
    CONTROLS.pop(started.analysis_id)
    monkeypatch.setattr(api, "runtime_instance_id", lambda: "new-process")
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM research_run_checkpoints").fetchall()
    status = api.get_analysis_status_payload(started.analysis_id)
    assert status["state"] == "stalled"
    assert status["resume_supported"] is False
    assert status["research"]["accounting_recovered"] is True
    assert {key: status["research"][key] for key in expected} == expected
    assert status["research"]["llm_calls"] == 2
    assert status["research"]["llm_usage_unknown"] == 1
    assert status["research"]["search_cost_estimate_by_currency"] == {"RUB": "0.125"}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM research_run_checkpoints").fetchall() == before
    assert started.analysis_id not in CONTROLS  # readback never resumes or recreates consent


def test_binding_is_immutable_and_recovery_does_not_leak_other_runs(tmp_path, monkeypatch):
    path = tmp_path / "runtime.db"
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(path))
    first, second = ResearchControl(owner_id=1), ResearchControl(owner_id=2)
    bind_analysis_control(first, "first")
    bind_analysis_control(second, "second")
    with bind_research(first):
        record_llm_start()
    with pytest.raises(SettingsConflict):
        bind_analysis_control(second, "first")
    assert recovered_research_snapshot("first")["llm_calls"] == 1
    assert recovered_research_snapshot("second")["llm_calls"] == 0
    assert recovered_research_snapshot("unknown") is None
    # An unrelated checkpoint payload must never be treated as public usage.
    first.checkpoint("settings", {"owner_id": 1, "secret": "must-not-leak"})
    result = recovered_research_snapshot("first")
    assert "owner_id" not in result and "secret" not in result
    assert result["llm_calls"] == 1
    for key in ("first", "second"):
        CONTROLS.pop(key, None)


def test_missing_or_corrupt_accounting_remains_unavailable_not_zero(tmp_path, monkeypatch):
    path = tmp_path / "runtime.db"
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(path))
    assert recovered_research_snapshot("old-run") is None
    assert not path.exists()
    control = ResearchControl()
    bind_analysis_control(control, "corrupt")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE research_run_checkpoints SET payload=? WHERE chunk_key='status'", ("invalid json",))
    assert recovered_research_snapshot("corrupt") is None
    CONTROLS.pop("corrupt", None)
