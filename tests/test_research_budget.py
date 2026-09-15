from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.research_budget import BudgetDenied, ResearchBudgetLedger
from app.research_settings import ResearchSettings, SettingsConflict


def ledger(tmp_path, **settings):
    store = ResearchBudgetLedger(tmp_path / "runtime.db")
    store.create_run(1, "run", ResearchSettings(**settings), snapshot_digest="sha256:test")
    return store


def reserve(store, request_id, amount="0.1", tokens=10):
    return store.reserve(1, "run", request_id, amount=amount, tokens=tokens,
                         currency="RUB", tariff_ref="provider:immutable-tariff")


def test_parallel_admissions_cannot_overspend_and_denial_survives_restart(tmp_path):
    store = ledger(tmp_path, cost_limit_amount="0.3")
    def admit(i):
        try:
            return reserve(store, str(i))["created"]
        except BudgetDenied:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(admit, range(20))) == 3
    restarted = ResearchBudgetLedger(store.path)
    assert Decimal(restarted.snapshot(1, "run")["committed_amount"]) == Decimal("0.3")
    assert restarted.snapshot(1, "run")["state"] == "paused"
    with pytest.raises(BudgetDenied):
        reserve(restarted, "later", "0")


def test_idempotency_and_exactly_one_dispatch(tmp_path):
    store = ledger(tmp_path)
    assert reserve(store, "request")["created"]
    assert not reserve(store, "request")["created"]
    with pytest.raises(SettingsConflict):
        reserve(store, "request", "0.2")
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda _: store.claim(1, "run", "request"), range(12))) == 1
    assert not store.release_unsent(1, "run", "request")
    assert not ResearchBudgetLedger(store.path).claim(1, "run", "request")


def test_settlement_releases_unused_bound_and_cannot_double_count(tmp_path):
    store = ledger(tmp_path, cost_limit_amount="1")
    reserve(store, "a", "0.8", 100)
    assert store.claim(1, "run", "a")
    receipt = dict(amount="0.2", tokens=25, receipt_ref="bill:a")
    store.settle(1, "run", "a", **receipt)
    store.settle(1, "run", "a", **receipt)
    reserve(store, "b", "0.8", 100)
    assert Decimal(store.snapshot(1, "run")["committed_amount"]) == 1
    assert store.snapshot(1, "run")["committed_tokens"] == 125
    with pytest.raises(SettingsConflict):
        store.settle(1, "run", "a", **{**receipt, "amount": "0.1"})


def test_missing_receipt_retains_reservation_after_restart(tmp_path):
    store = ledger(tmp_path, cost_limit_amount="1")
    reserve(store, "timeout", "0.8")
    store.claim(1, "run", "timeout")
    with pytest.raises(ValueError, match="incomplete_receipt"):
        store.settle(1, "run", "timeout", amount=None, tokens=None, receipt_ref="timeout")
    restarted = ResearchBudgetLedger(store.path)
    with pytest.raises(BudgetDenied, match="cost_limit"):
        reserve(restarted, "retry", "0.3")


def test_only_unsent_reservations_can_be_released(tmp_path):
    store = ledger(tmp_path)
    reserve(store, "a")
    assert store.release_unsent(1, "run", "a")
    assert not store.claim(1, "run", "a")
    assert Decimal(store.snapshot(1, "run")["committed_amount"]) == 0
    with pytest.raises(SettingsConflict):
        store.settle(1, "run", "a", amount="0", tokens=0, receipt_ref="not-sent")


def test_unknown_price_blocks_by_default_and_is_never_reported_as_zero(tmp_path):
    store = ledger(tmp_path)
    with pytest.raises(BudgetDenied, match="price_unknown"):
        reserve(store, "a", None)
    store.create_run(2, "run", ResearchSettings(unknown_price_action="allow_unpriced"), snapshot_digest="sha256:2")
    store.reserve(2, "run", "a", amount=None, tokens=None, currency="RUB", tariff_ref=None)
    view = store.snapshot(2, "run")
    assert view["committed_amount"] is None and view["committed_tokens"] is None
    assert view["cost_unknown"] and view["tokens_unknown"]


@pytest.mark.parametrize("field,kwargs,reason", [
    ("tokens", {"token_limit": 9}, "token_limit"),
    ("unknown", {"token_limit": 10}, "token_bound_unknown"),
    ("cost", {"cost_warning_amount": "0.1", "threshold_action": "pause"}, "cost_warning"),
    ("token_warning", {"token_warning": 10, "threshold_action": "pause"}, "token_warning"),
])
def test_thresholds_pause_before_dispatch(tmp_path, field, kwargs, reason):
    store = ledger(tmp_path, **kwargs)
    with pytest.raises(BudgetDenied, match=reason):
        reserve(store, "a", tokens=None if field == "unknown" else 10)
    assert store.snapshot(1, "run")["committed_tokens"] == 0


def test_warning_can_notify_and_continue_but_hard_stop_is_sticky(tmp_path):
    store = ledger(tmp_path, token_warning=10, token_limit=20, hard_limit_action="stop")
    assert reserve(store, "a")["warning"] == "token_warning"
    assert reserve(store, "b")["created"]
    with pytest.raises(BudgetDenied):
        reserve(store, "c")
    assert store.snapshot(1, "run")["state"] == "stopped"
    with pytest.raises(BudgetDenied):
        store.claim(1, "run", "b")


def test_provider_bound_violation_keeps_actual_bill_and_stops_run(tmp_path):
    store = ledger(tmp_path, cost_limit_amount="0.2")
    reserve(store, "a", "0.1")
    store.claim(1, "run", "a")
    store.settle(1, "run", "a", amount="0.3", tokens=30, receipt_ref="bill:a")
    view = store.snapshot(1, "run")
    assert view["state"] == "stopped" and view["reason"] == "provider_bound_exceeded"
    assert Decimal(view["committed_amount"]) == Decimal("0.3")


def test_run_policy_and_owner_cannot_be_swapped(tmp_path):
    store = ledger(tmp_path)
    with pytest.raises(LookupError):
        store.snapshot(2, "run")
    with pytest.raises(LookupError):
        store.claim(2, "run", "a")
    with pytest.raises(SettingsConflict):
        store.create_run(1, "run", ResearchSettings(token_limit=1), snapshot_digest="sha256:test")
    with pytest.raises(ValueError, match="currency"):
        store.reserve(1, "run", "a", amount="1", tokens=1, currency="USD", tariff_ref="usd-tariff")


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0.0000001", 0.1, True])
def test_invalid_or_inexact_money_cannot_enter_ledger(tmp_path, amount):
    store = ledger(tmp_path)
    with pytest.raises(ValueError):
        reserve(store, "a", amount)
