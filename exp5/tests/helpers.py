from dataclasses import replace
from datetime import date

from exp5.gate import (BaseConfig, Budget, Gate, Ledger, Limits, ListItem, Quote)

KEY = b"test-owner-key"
TODAY = date(2026, 10, 12)


def make_cfg(limits=None, ceiling=500_000, **kw):
    limits = limits or Limits(hug_margin_bp=0)     # boundary tests: hug bump off
    items = {
        "drill": ListItem("drill", "Cordless drill", 10, date(2026, 11, 30), "tools"),
        "paper": ListItem("paper", "Printer paper", 100, date(2026, 11, 30), "general"),
        "tile": ListItem("tile", "Floor tile", 100, date(2026, 10, 20), "home-repair"),
    }
    base = dict(limits=limits,
                budget=Budget(ceiling, date(2026, 10, 1), date(2026, 12, 31)),
                items=items,
                vendors=frozenset({"Amazon", "HomeDepot", "Costco", "Walmart"}),
                accounts=frozenset({"checking", "card"}), currency="USD")
    base.update(kw)
    return BaseConfig(**base)


def make_quote(qid="q1", item="paper", vendor="Amazon", unit=1000, rel=9500,
               days=3, tax_bp=0, ship=0, fees=0, cur="USD", expires=date(2026, 10, 31)):
    return Quote(qid, item, vendor, unit, tax_bp, ship, fees, cur, rel, days, expires)


def make_gate(cfg=None, packs=(), balances=None):
    cfg = cfg or make_cfg()
    led = Ledger()
    for a, c in (balances or {"checking": 1_000_000, "card": 0}).items():
        led.set_balance(a, c)
    return Gate(cfg, list(packs), led, KEY), led


def quotes_of(*qs):
    return {q.quote_id: q for q in qs}


def approve(led, aid, kind="purchase", **terms):
    t = {"kind": kind, "expires": "2026-12-31"}
    t.update(terms)
    led.issue_approval(KEY, aid, t)
