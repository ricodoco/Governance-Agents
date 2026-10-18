"""Plain data types shared by the gate, packs, and ledger."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

LEVEL_ORDER = {"A": 0, "B": 1, "C": 2}

# Verdicts
EXECUTE = "EXECUTE"                        # level A: act, log only
EXECUTE_AND_NOTIFY = "EXECUTE_AND_NOTIFY"  # level B: act, then notify owner
EXECUTE_APPROVED = "EXECUTE_APPROVED"      # level C with a valid owner approval
REQUIRE_APPROVAL = "REQUIRE_APPROVAL"      # level C, no valid approval: do not act
DENY = "DENY"                              # hard rule violated: not approvable
EXECUTING = (EXECUTE, EXECUTE_AND_NOTIFY, EXECUTE_APPROVED)


def max_level(a: str, b: str) -> str:
    return a if LEVEL_ORDER[a] >= LEVEL_ORDER[b] else b


@dataclass(frozen=True)
class Limits:
    """Tunable limits. Packs may only tighten these relative to the owner's base."""
    a_max_cents: int = 5_000            # <= $50.00 -> level A (inclusive)
    b_max_cents: int = 50_000           # <= $500.00 -> level B (inclusive); above -> C
    window_days: int = 7
    window_cap_cents: int = 100_000     # total spend per window above this -> C
    hug_margin_bp: int = 200            # within 2% below a threshold -> bump one level
    delta_bp: int = 200                 # alt vendor reliability within 2 points of best
    gamma_bp: int = 1_500               # ...and landed cost >= 15% lower
    delivery_margin_days: int = 1
    c_categories: frozenset = frozenset()  # categories that always need approval


@dataclass(frozen=True)
class Budget:
    ceiling_cents: int
    period_start: date
    period_end: date


@dataclass(frozen=True)
class ListItem:
    item_id: str
    name: str
    qty: int            # total quantity the owner has authorized
    nlt: date           # no-later-than delivery date
    category: str = "general"


@dataclass(frozen=True)
class BaseConfig:
    """Owner-controlled. The proposer never gets write access to this."""
    limits: Limits
    budget: Budget
    items: dict
    vendors: frozenset
    accounts: frozenset
    currency: str = "USD"


@dataclass(frozen=True)
class Quote:
    """Issued by a trusted quote source (sim vendor / real read-only lookup)."""
    quote_id: str
    item_id: str
    vendor: str
    unit_cents: int
    tax_bp: int
    shipping_cents: int
    fees_cents: int
    currency: str
    reliability_bp: int     # observed on-time rate, 0..10000
    delivery_days: int
    expires: date           # last valid day (inclusive)


@dataclass(frozen=True)
class PurchaseProposal:
    item_id: str
    quote_id: str
    qty: int
    approval_id: Optional[str] = None


@dataclass(frozen=True)
class Request:
    """What the principal asked for, as structured input from the task channel. It never comes from the proposer or from message text."""
    item_id: str
    qty: int


@dataclass(frozen=True)
class TransferProposal:
    from_acct: str
    to_acct: str
    amount_cents: int
    for_quote_id: str       # the gate computes the documented need from this quote
    for_qty: int
    approval_id: Optional[str] = None


@dataclass(frozen=True)
class Approval:
    approval_id: str
    terms: dict
    sig: str
    used: bool = False


@dataclass(frozen=True)
class State:
    """Snapshot of ledger facts the pure evaluator needs."""
    spent_period_cents: int = 0
    cum_item_window: dict = field(default_factory=dict)
    window_total_cents: int = 0
    transfer_window_total_cents: int = 0
    ordered_qty: dict = field(default_factory=dict)
    balances: dict = field(default_factory=dict)
    approvals: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    verdict: str
    level: Optional[str] = None
    landed_cents: Optional[int] = None
    reasons: tuple = ()
    approval_id: Optional[str] = None

    @property
    def executes(self) -> bool:
        return self.verdict in EXECUTING

    @property
    def notify(self) -> bool:
        return self.verdict in (EXECUTE_AND_NOTIFY, EXECUTE_APPROVED)
