import asyncio
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.research_control import ResearchControl, bind_research, record_search_attempt
from app.research_execution import run_controlled, ResearchInterrupted
from app.research_settings import ResearchSettings
from app.search_gateway.gateway import SearchGateway
from app.search_gateway.models import SearchPolicy, SearchRequest
from app.search_gateway.providers import SearchProvider, ProviderError


def test_estimates_preserve_currency_precision_and_unknown_prices(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    control = ResearchControl()
    with bind_research(control):
        for currency, amount, paid in [("RUB", "0.1", True), ("RUB", "0.2", True),
                                       ("USD", "0.01", True), ("RUB", "0", True),
                                       ("USD", "NaN", True), ("EUR", "0", False)]:
            record_search_attempt(SimpleNamespace(cost_currency=currency, cost_amount=Decimal(amount), paid=paid))
    snapshot = control.snapshot()
    assert snapshot["search_cost_estimate_by_currency"] == {"RUB": "0.3", "USD": "0.01", "EUR": "0"}
    assert snapshot["search_price_unknown"] == 2
    assert snapshot["search_attempts"] == 6
    assert snapshot["monetary_cost"] == "not_reported"
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        saved = json.loads(db.execute("SELECT payload FROM research_run_checkpoints WHERE chunk_key='search_usage'").fetchone()[0])
    assert saved == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_gateway_accounts_each_dispatched_attempt_even_when_cancelled(tmp_path, monkeypatch, cancel):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    class Provider(SearchProvider):
        name = "fake"
        paid = True
        configured = True
        cost_amount = Decimal("0.125")
        cost_currency = "RUB"
        async def search(self, request, *, timeout_seconds):
            if cancel:
                await asyncio.Event().wait()
            raise ProviderError("retryable")
    control = ResearchControl(settings=ResearchSettings(
        retry_count=2, retry_backoff_seconds=0.001,
        mission_timeout_seconds=0.02 if cancel else None, hard_limit_action="stop"))
    gateway = SearchGateway([Provider()])
    operation = lambda: gateway.search(SearchRequest(query="test", mission_id="m", correlation_id="c"),
        SearchPolicy(provider_order=("fake",), max_cost_by_currency={"RUB": Decimal("100")}))
    if cancel:
        with pytest.raises(ResearchInterrupted):
            await run_controlled(control, operation)
    else:
        await run_controlled(control, operation)
    assert control.search_attempts == (1 if cancel else 3)
    assert control.search_estimates == {"RUB": Decimal("0.125") * control.search_attempts}


@pytest.mark.asyncio
async def test_gateway_denied_work_is_not_counted_as_spending(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMETON_RUNTIME_DB", str(tmp_path / "runtime.db"))
    class Provider(SearchProvider):
        name = "fake"
        paid = True
        configured = True
        execution_allowed = False
        cost_amount = Decimal("1")
        cost_currency = "RUB"
        async def search(self, request, *, timeout_seconds):
            pytest.fail("denied provider must not execute")
    control = ResearchControl(settings=ResearchSettings())
    with bind_research(control):
        await SearchGateway([Provider()]).search(
            SearchRequest(query="test", mission_id="m", correlation_id="c"),
            SearchPolicy(provider_order=("fake",), max_cost_by_currency={"RUB": Decimal("100")}))
    assert control.search_attempts == 0
    assert control.search_estimates == {}
