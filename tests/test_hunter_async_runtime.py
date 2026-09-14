from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

import app.main as main
from app.models import HuntRequest
from app.hunter_async_runtime import (
    clear_hunter_runs_for_tests,
    get_hunter_run,
    start_hunter_run,
    stop_hunter_run,
)


@pytest.fixture(autouse=True)
def clear_runs():
    clear_hunter_runs_for_tests()
    yield
    clear_hunter_runs_for_tests()


@pytest.mark.asyncio
async def test_long_hunter_run_continues_until_explicit_stop_and_keeps_completed_work():
    waiting = asyncio.Event()

    async def runner(progress):
        progress({
            "phase": "inspecting_candidates",
            "raw_results": 3,
            "queries": ["стоматология Красноярск"],
            "total_candidates": 3,
            "processed_candidates": 1,
            "candidates": [{"company_name": "Готовая клиника", "url": "https://clinic.ru"}],
        })
        await waiting.wait()
        return {}

    started = start_hunter_run(
        region="Красноярск",
        search_zone=None,
        requested_regime="auto",
        runner=runner,
    )
    await asyncio.sleep(0)

    running = get_hunter_run(started["run_id"], started["run_token"])
    assert running["state"] == "running"
    assert running["progress"]["processed_candidates"] == 1

    stopped_request = stop_hunter_run(started["run_id"], started["run_token"])
    assert stopped_request["state"] == "stopped"
    await asyncio.sleep(0)

    stopped = get_hunter_run(started["run_id"], started["run_token"])
    assert stopped["state"] == "stopped"
    assert stopped["result"]["candidates"][0]["company_name"] == "Готовая клиника"
    assert stopped["result"]["funnel"]["inspected_candidates"] == 1
    assert "остановлен пользователем" in stopped["result"]["notes"][0]


@pytest.mark.asyncio
async def test_hunter_run_completes_without_runtime_deadline():
    async def runner(progress):
        await asyncio.sleep(0.02)
        progress({"phase": "inspecting_candidates", "processed_candidates": 1, "total_candidates": 1})
        return {"region": "Россия", "candidates": [{"company_name": "Клиника"}]}

    started = start_hunter_run(
        region="Россия",
        search_zone=None,
        requested_regime="discovery",
        runner=runner,
    )
    await asyncio.sleep(0.04)

    completed = get_hunter_run(started["run_id"], started["run_token"])
    assert completed["state"] == "completed"
    assert completed["result"]["candidates"][0]["company_name"] == "Клиника"


@pytest.mark.asyncio
async def test_hunter_run_token_is_required():
    async def runner(_progress):
        return {}

    started = start_hunter_run(
        region="Россия",
        search_zone=None,
        requested_regime="auto",
        runner=runner,
    )
    with pytest.raises(KeyError):
        get_hunter_run(started["run_id"], "wrong-token")


@pytest.mark.asyncio
async def test_hunter_async_api_starts_polls_and_stops(monkeypatch):
    waiting = asyncio.Event()

    async def fake_payload(_req, _regime, progress_callback=None):
        progress_callback({"phase": "inspecting_candidates", "processed_candidates": 0, "total_candidates": 2})
        await waiting.wait()
        return {}

    monkeypatch.setattr(main, "_run_hunt_payload", fake_payload)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/hunt/start",
        "query_string": b"search_regime=discovery",
        "headers": [],
    })
    started = await main.start_hunt(
        HuntRequest(region="Россия", industries=["стоматология"]),
        request,
    )
    await asyncio.sleep(0)

    status_payload = await main.hunt_status(started["run_id"], started["run_token"])
    assert status_payload["state"] == "running"
    assert status_payload["progress"]["total_candidates"] == 2

    stopped = await main.stop_hunt(started["run_id"], started["run_token"])
    assert stopped["state"] == "stopped"
