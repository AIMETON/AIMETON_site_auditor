"""Durable admission accounting, not a provider dispatcher or tariff authority.

Internal API only. A trusted adapter must supply an authoritative upper bound for
the entire billable request (including reasoning/cache/tool fees), not an average
price estimate. Runtime wiring and tariff resolution are separate delivery gates.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from app.research_settings import ResearchSettings, SettingsConflict


class BudgetDenied(RuntimeError):
    pass


def _money(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, (Decimal, str)):
        raise ValueError("money_requires_decimal_or_string")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid_amount")
    # Match the persisted preference precision; never round a bound down.
    if amount.as_tuple().exponent < -6 or amount >= Decimal("10000000000"):
        raise ValueError("amount_out_of_range")
    return amount


def _tokens(value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("invalid_token_count")
    return value


class ResearchBudgetLedger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS research_budget_runs (
                owner_id INTEGER NOT NULL, run_id TEXT NOT NULL, settings TEXT NOT NULL,
                snapshot_digest TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                PRIMARY KEY(owner_id, run_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS research_budget_requests (
                owner_id INTEGER NOT NULL, run_id TEXT NOT NULL, request_id TEXT NOT NULL,
                quote TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT,
                PRIMARY KEY(owner_id, run_id, request_id))""")

    def create_run(self, owner_id: int, run_id: str, settings: ResearchSettings,
                   *, snapshot_digest: str) -> None:
        if not run_id or not snapshot_digest:
            raise ValueError("run_and_snapshot_required")
        payload = ResearchSettings.model_validate(settings.model_dump()).model_dump_json()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT settings, snapshot_digest FROM research_budget_runs WHERE owner_id=? AND run_id=?",
                             (owner_id, run_id)).fetchone()
            if row and row != (payload, snapshot_digest):
                raise SettingsConflict("budget_run_conflict")
            db.execute("INSERT OR IGNORE INTO research_budget_runs VALUES (?, ?, ?, ?, 'running', NULL)",
                       (owner_id, run_id, payload, snapshot_digest))

    @staticmethod
    def _run(db, owner_id, run_id):
        row = db.execute("SELECT settings, state, reason FROM research_budget_runs WHERE owner_id=? AND run_id=?",
                         (owner_id, run_id)).fetchone()
        if not row:
            raise LookupError("budget_run_not_found")
        return ResearchSettings.model_validate_json(row[0]), row[1], row[2]

    @staticmethod
    def _totals(db, owner_id, run_id):
        amount, tokens, cost_unknown, tokens_unknown = Decimal(0), 0, False, False
        for quote, state, receipt in db.execute(
            "SELECT quote, state, receipt FROM research_budget_requests WHERE owner_id=? AND run_id=?",
            (owner_id, run_id),
        ):
            if state == "released":
                continue
            value = json.loads(receipt if state == "settled" else quote)
            # An unknown outcome retains the entire reservation, even after restart.
            cost_unknown |= value["amount"] is None
            tokens_unknown |= value["tokens"] is None
            amount += Decimal(value["amount"]) if value["amount"] is not None else 0
            tokens += value["tokens"] or 0
        return amount, tokens, cost_unknown, tokens_unknown

    def reserve(self, owner_id: int, run_id: str, request_id: str, *,
                amount: Decimal | str | None, tokens: int | None,
                currency: str, tariff_ref: str | None) -> dict:
        amount, tokens = _money(amount), _tokens(tokens)
        if not request_id:
            raise ValueError("request_id_required")
        if amount is not None and not tariff_ref:
            raise ValueError("known_price_requires_tariff_provenance")
        quote = json.dumps({"amount": str(amount) if amount is not None else None,
                            "tokens": tokens, "currency": currency, "tariff_ref": tariff_ref}, sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            settings, state, reason = self._run(db, owner_id, run_id)
            old = db.execute("SELECT quote, state FROM research_budget_requests WHERE owner_id=? AND run_id=? AND request_id=?",
                             (owner_id, run_id, request_id)).fetchone()
            if old:
                if old[0] != quote:
                    raise SettingsConflict("reservation_conflict")
                return {"state": old[1], "created": False}
            if state != "running":
                raise BudgetDenied(reason or state)
            if currency != settings.currency:
                raise ValueError("currency_mismatch")
            used, used_tokens, unknown_cost, unknown_tokens = self._totals(db, owner_id, run_id)
            denied = None
            warning = None
            if (unknown_cost or amount is None) and (
                settings.unknown_price_action == "pause" or settings.cost_limit_amount is not None
                or settings.cost_warning_amount is not None
            ):
                denied = "price_unknown"
            elif (unknown_tokens or tokens is None) and (settings.token_limit is not None or settings.token_warning is not None):
                denied = "token_bound_unknown"
            elif settings.cost_limit_amount is not None and used + amount > settings.cost_limit_amount:
                denied = "cost_limit"
            elif settings.token_limit is not None and used_tokens + tokens > settings.token_limit:
                denied = "token_limit"
            elif settings.cost_warning_amount is not None and used + amount >= settings.cost_warning_amount:
                warning = "cost_warning"
            elif settings.token_warning is not None and used_tokens + tokens >= settings.token_warning:
                warning = "token_warning"
            if warning and settings.threshold_action == "pause":
                denied = warning
            if denied:
                state = "paused" if denied in {"price_unknown", "token_bound_unknown", "cost_warning", "token_warning"} else (
                    "paused" if settings.hard_limit_action == "pause" else "stopped")
                db.execute("UPDATE research_budget_runs SET state=?, reason=? WHERE owner_id=? AND run_id=?",
                           (state, denied, owner_id, run_id))
            else:
                db.execute("INSERT INTO research_budget_requests VALUES (?, ?, ?, ?, 'reserved', NULL)",
                           (owner_id, run_id, request_id, quote))
        # Raise after commit: a denied admission must survive a process restart.
        if denied:
            raise BudgetDenied(denied)
        return {"state": "reserved", "created": True, "warning": warning}

    def claim(self, owner_id: int, run_id: str, request_id: str) -> bool:
        """Exactly one caller may dispatch. Crash after claim keeps the reservation."""
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            _, state, reason = self._run(db, owner_id, run_id)
            if state != "running":
                raise BudgetDenied(reason or state)
            return db.execute("""UPDATE research_budget_requests SET state='dispatched'
                WHERE owner_id=? AND run_id=? AND request_id=? AND state='reserved'""",
                (owner_id, run_id, request_id)).rowcount == 1

    def release_unsent(self, owner_id: int, run_id: str, request_id: str) -> bool:
        """Only proven unsent work can release funds without a provider receipt."""
        with sqlite3.connect(self.path) as db:
            self._run(db, owner_id, run_id)
            return db.execute("""UPDATE research_budget_requests SET state='released'
                WHERE owner_id=? AND run_id=? AND request_id=? AND state='reserved'""",
                (owner_id, run_id, request_id)).rowcount == 1

    def settle(self, owner_id: int, run_id: str, request_id: str, *,
               amount: Decimal | str | None, tokens: int | None, receipt_ref: str) -> None:
        amount, tokens = _money(amount), _tokens(tokens)
        if not receipt_ref:
            raise ValueError("receipt_provenance_required")
        receipt = json.dumps({"amount": str(amount) if amount is not None else None,
                              "tokens": tokens, "receipt_ref": receipt_ref}, sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._run(db, owner_id, run_id)
            row = db.execute("SELECT quote, state, receipt FROM research_budget_requests WHERE owner_id=? AND run_id=? AND request_id=?",
                             (owner_id, run_id, request_id)).fetchone()
            if not row:
                raise LookupError("reservation_not_found")
            if row[1] == "settled" and row[2] == receipt:
                return
            if row[1] != "dispatched":
                raise SettingsConflict("settlement_conflict")
            if amount is None or tokens is None:
                # Missing usage is not a zero-cost receipt; retain the full bound.
                raise ValueError("incomplete_receipt_keeps_reservation")
            quote = json.loads(row[0])
            violation = (quote["amount"] is not None and amount > Decimal(quote["amount"])) or (
                quote["tokens"] is not None and tokens > quote["tokens"])
            db.execute("UPDATE research_budget_requests SET state='settled', receipt=? WHERE owner_id=? AND run_id=? AND request_id=?",
                       (receipt, owner_id, run_id, request_id))
            if violation:
                # Preserve the real bill, even when an upstream bound was wrong.
                db.execute("UPDATE research_budget_runs SET state='stopped', reason='provider_bound_exceeded' WHERE owner_id=? AND run_id=?",
                           (owner_id, run_id))

    def snapshot(self, owner_id: int, run_id: str) -> dict:
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN")
            settings, state, reason = self._run(db, owner_id, run_id)
            amount, tokens, unknown_cost, unknown_tokens = self._totals(db, owner_id, run_id)
            return {"state": state, "reason": reason, "currency": settings.currency,
                    "committed_amount": None if unknown_cost else str(amount),
                    "committed_tokens": None if unknown_tokens else tokens,
                    "cost_unknown": unknown_cost, "tokens_unknown": unknown_tokens}
