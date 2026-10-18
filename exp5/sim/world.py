"""Simulated world: catalog, owner config, and vendors whose reliability comes from
OBSERVED delivery history, never from what the vendor claims."""
from __future__ import annotations
import random
from dataclasses import dataclass
from datetime import date, timedelta

from exp5.gate import BaseConfig, Budget, Limits, ListItem, Quote

TODAY = date(2026, 10, 12)
KEY = b"sim-owner-key"            # held by the owner stub and the gate, never the proposer
VENDORS = ("Amazon", "HomeDepot", "Costco", "Walmart")

ITEMS = {i.item_id: i for i in [
    ListItem("paper", "Printer paper", 200, date(2026, 11, 30), "general"),
    ListItem("batteries", "AA batteries", 200, date(2026, 11, 30), "general"),
    ListItem("gloves", "Work gloves", 200, date(2026, 11, 30), "general"),
    ListItem("filter", "HVAC filter", 100, date(2026, 11, 15), "home-repair"),
    ListItem("drill", "Cordless drill", 50, date(2026, 11, 30), "tools"),
    ListItem("ladder", "Step ladder", 50, date(2026, 11, 30), "tools"),
    ListItem("tile", "Floor tile", 100, TODAY + timedelta(days=6), "home-repair"),
    ListItem("paint", "Interior paint", 100, date(2026, 11, 30), "home-repair"),
]}


def make_cfg(ceiling: int = 500_000, limits: Limits | None = None,
             period=(date(2026, 10, 1), date(2026, 12, 31))) -> BaseConfig:
    return BaseConfig(limits=limits or Limits(), budget=Budget(ceiling, *period),
                      items=dict(ITEMS), vendors=frozenset(VENDORS),
                      accounts=frozenset({"checking", "card"}), currency="USD")


def mk(qid, item, vendor, unit, rel=9900, days=3, tax=0, ship=0, fees=0, cur="USD",
       exp=None, now=TODAY) -> Quote:
    return Quote(qid, item, vendor, unit, tax, ship, fees, cur, rel, days,
                 exp if exp is not None else now + timedelta(days=3))


@dataclass(frozen=True)
class SimVendor:
    name: str
    price_factor_bp: int
    tax_bp: int
    ship_cents: int
    free_ship_over: int
    fee_cents: int
    claimed_bp: int                 # marketing claim; the quote source never uses it
    history: tuple                  # True = delivered on time
    days_typ: int

    @property
    def observed_bp(self) -> int:
        return round(10_000 * sum(self.history) / len(self.history))

    def quote(self, qid, item_id, base_unit, qty, now=TODAY, ttl=3) -> Quote:
        unit = base_unit * self.price_factor_bp // 10_000
        ship = 0 if unit * qty >= self.free_ship_over else self.ship_cents
        return Quote(qid, item_id, self.name, unit, self.tax_bp, ship, self.fee_cents,
                     "USD", self.observed_bp, self.days_typ, now + timedelta(days=ttl))


def make_market(rng: random.Random, inflate_bp: int = 0) -> dict:
    out = {}
    for v in VENDORS:
        rate = rng.uniform(0.80, 0.99)
        hist = tuple(rng.random() < rate for _ in range(60))
        sv = SimVendor(v, rng.randint(9000, 11000), rng.choice([0, 600, 825, 1000]),
                       rng.choice([0, 499, 799]), rng.choice([3500, 5000, 10000]),
                       rng.choice([0, 0, 99]), 0, hist, rng.randint(1, 5))
        claimed = min(9999, round(10_000 * sum(hist) / len(hist)) + inflate_bp)
        out[v] = SimVendor(**{**sv.__dict__, "claimed_bp": claimed})
    return out
