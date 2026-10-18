"""SQLite ledger: orders, transfers, accounts, approvals, and a hash-chained append-only log."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta

from .approvals import sign_terms
from .models import Approval, BaseConfig, Limits, State

SCHEMA = """
CREATE TABLE IF NOT EXISTS log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS log_no_update BEFORE UPDATE ON log
  BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log
  BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TABLE IF NOT EXISTS orders(
  order_id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
  item_id TEXT NOT NULL, vendor TEXT NOT NULL, qty INTEGER NOT NULL,
  landed_cents INTEGER NOT NULL, level TEXT NOT NULL, approval_id TEXT);
CREATE TABLE IF NOT EXISTS transfers(
  transfer_id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
  from_acct TEXT NOT NULL, to_acct TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  level TEXT NOT NULL, approval_id TEXT);
CREATE TABLE IF NOT EXISTS accounts(
  acct_id TEXT PRIMARY KEY,
  balance_cents INTEGER NOT NULL CHECK(balance_cents >= 0));
CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY, terms TEXT NOT NULL, sig TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0);
"""
GENESIS = "0" * 64


def _h(prev: str, day: str, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{day}|{kind}|{payload}".encode()).hexdigest()


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        """BEGIN IMMEDIATE transaction; re-entrant (inner calls join the outer one)."""
        if self.db.in_transaction:
            yield
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ---- append-only log -------------------------------------------------
    def append_log(self, day: date, kind: str, payload: dict) -> None:
        with self.tx():
            row = self.db.execute(
                "SELECT hash FROM log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            body = json.dumps(payload, sort_keys=True, default=str)
            self.db.execute(
                "INSERT INTO log(day,kind,payload,prev_hash,hash) VALUES(?,?,?,?,?)",
                (day.isoformat(), kind, body, prev, _h(prev, day.isoformat(), kind, body)))

    def read_log(self) -> list:
        rows = self.db.execute(
            "SELECT seq,day,kind,payload FROM log ORDER BY seq").fetchall()
        return [(s, d, k, json.loads(p)) for s, d, k, p in rows]

    def verify_chain(self) -> bool:
        prev = GENESIS
        for _, day, kind, payload, ph, h in self.db.execute(
                "SELECT seq,day,kind,payload,prev_hash,hash FROM log ORDER BY seq"):
            if ph != prev or h != _h(prev, day, kind, payload):
                return False
            prev = h
        return True

    # ---- owner-side operations (the proposer is never handed these) -------
    def set_balance(self, acct_id: str, cents: int) -> None:
        self.db.execute(
            "INSERT INTO accounts(acct_id,balance_cents) VALUES(?,?) "
            "ON CONFLICT(acct_id) DO UPDATE SET balance_cents=excluded.balance_cents",
            (acct_id, cents))

    def issue_approval(self, key: bytes, approval_id: str, terms: dict) -> None:
        self.db.execute(
            "INSERT INTO approvals(approval_id,terms,sig) VALUES(?,?,?)",
            (approval_id, json.dumps(terms, sort_keys=True), sign_terms(key, terms)))

    # ---- gate-side commits -----------------------------------------------
    def record_order(self, day, item_id, vendor, qty, landed, level, approval_id):
        self.db.execute(
            "INSERT INTO orders(day,item_id,vendor,qty,landed_cents,level,approval_id) "
            "VALUES(?,?,?,?,?,?,?)",
            (day.isoformat(), item_id, vendor, qty, landed, level, approval_id))

    def record_transfer(self, day, from_acct, to_acct, amount, level, approval_id):
        self.db.execute(
            "UPDATE accounts SET balance_cents = balance_cents - ? WHERE acct_id=?",
            (amount, from_acct))
        self.db.execute(
            "UPDATE accounts SET balance_cents = balance_cents + ? WHERE acct_id=?",
            (amount, to_acct))
        self.db.execute(
            "INSERT INTO transfers(day,from_acct,to_acct,amount_cents,level,approval_id) "
            "VALUES(?,?,?,?,?,?)",
            (day.isoformat(), from_acct, to_acct, amount, level, approval_id))

    def mark_approval_used(self, approval_id: str) -> None:
        self.db.execute("UPDATE approvals SET used=1 WHERE approval_id=?", (approval_id,))

    # ---- state snapshot ----------------------------------------------------
    def snapshot(self, cfg: BaseConfig, limits: Limits, now: date) -> State:
        q = self.db.execute
        b = cfg.budget
        spent = q("SELECT COALESCE(SUM(landed_cents),0) FROM orders WHERE day>=? AND day<=?",
                  (b.period_start.isoformat(), b.period_end.isoformat())).fetchone()[0]
        w0 = (now - timedelta(days=limits.window_days)).isoformat()
        w1 = now.isoformat()
        cum = {i: s for i, s in q(
            "SELECT item_id,SUM(landed_cents) FROM orders WHERE day>? AND day<=? "
            "GROUP BY item_id", (w0, w1))}
        wtot = q("SELECT COALESCE(SUM(landed_cents),0) FROM orders WHERE day>? AND day<=?",
                 (w0, w1)).fetchone()[0]
        ttot = q("SELECT COALESCE(SUM(amount_cents),0) FROM transfers WHERE day>? AND day<=?",
                 (w0, w1)).fetchone()[0]
        ordered = {i: s for i, s in q("SELECT item_id,SUM(qty) FROM orders GROUP BY item_id")}
        bal = {a: c for a, c in q("SELECT acct_id,balance_cents FROM accounts")}
        apps = {a: Approval(a, json.loads(t), s, bool(u)) for a, t, s, u in q(
            "SELECT approval_id,terms,sig,used FROM approvals")}
        return State(spent, cum, wtot, ttot, ordered, bal, apps)
