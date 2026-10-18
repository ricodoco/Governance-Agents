"""The deterministic gate. No LLM, no network, no floats.

The proposer (an LLM) emits structured proposals. The gate computes landed cost
itself from a trusted Quote, checks every rule, and returns a Decision. Anything
unclassifiable or erroring fails closed (DENY or REQUIRE_APPROVAL), never open.

Levels: A <= a_max (execute, log), B <= b_max (execute, notify), C above (approval).
Thresholds are inclusive: exactly $50.00 is A, exactly $500.00 is B.
Hard rules (DENY, never approvable): item not on list, quantity over list, unknown
or mismatched quote, unapproved vendor, wrong currency, expired quote, budget
period inactive, budget ceiling exceeded, bad transfer accounts or amount.
Two rules added after the injection test: when the caller passes a Request (the principal's
structured ask), a purchase of another item or another quantity is denied (item_not_requested,
qty_not_requested); and at levels A and B a cited approval is denied (approval_not_needed),
because an approval id that nothing needs can only be a claim.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional

from .approvals import signature_ok
from .ledger import Ledger
from .models import (DENY, EXECUTE, EXECUTE_AND_NOTIFY, EXECUTE_APPROVED, EXECUTING,
                     LEVEL_ORDER, REQUIRE_APPROVAL, BaseConfig, Decision, Limits,
                     PurchaseProposal, Quote, Request, State, TransferProposal, max_level)
from .money import landed_cents
from .packs import Pack, compose


def deny(*reasons: str) -> Decision:
    return Decision(DENY, None, None, tuple(reasons))


# ---------------------------------------------------------------- tiering
def tier(amount: int, limits: Limits) -> str:
    if amount <= limits.a_max_cents:
        return "A"
    if amount <= limits.b_max_cents:
        return "B"
    return "C"


def _hugging(amount: int, threshold: int, hug_bp: int) -> bool:
    """True if amount is at or below threshold but within hug_bp basis points of it."""
    return amount <= threshold and amount * 10_000 > threshold * (10_000 - hug_bp)


def amount_level(amount: int, limits: Limits) -> str:
    """Tier by amount; landing just under a threshold bumps one level toward C."""
    base = tier(amount, limits)
    if base == "A" and _hugging(amount, limits.a_max_cents, limits.hug_margin_bp):
        return "B"
    if base == "B" and _hugging(amount, limits.b_max_cents, limits.hug_margin_bp):
        return "C"
    return base


# -------------------------------------------------------------- approvals
def _approval_check(p, state: State, key: bytes, now: date, kind: str, match) -> tuple:
    if not p.approval_id:
        return False, "no_approval"
    ap = state.approvals.get(p.approval_id)
    if ap is None:
        return False, "approval_unknown"
    if ap.used:
        return False, "approval_already_used"
    if not signature_ok(key, ap.terms, ap.sig):
        return False, "approval_bad_signature"
    t = ap.terms
    if t.get("kind") != kind:
        return False, "approval_wrong_kind"
    try:
        if now > date.fromisoformat(t["expires"]):
            return False, "approval_expired"
        if not match(t):
            return False, "approval_terms_mismatch"
    except (KeyError, ValueError, TypeError):
        return False, "approval_malformed"
    return True, ""


def _finish(level: str, reasons: list, landed, p, state, key, now, kind, match) -> Decision:
    if level in ("A", "B") and p.approval_id:
        return deny("approval_not_needed")
    if level == "A":
        return Decision(EXECUTE, "A", landed, tuple(reasons))
    if level == "B":
        return Decision(EXECUTE_AND_NOTIFY, "B", landed, tuple(reasons))
    ok, why = _approval_check(p, state, key, now, kind, match)
    if ok:
        return Decision(EXECUTE_APPROVED, "C", landed, tuple(reasons), p.approval_id)
    return Decision(REQUIRE_APPROVAL, "C", landed, tuple(reasons) + (why,))


# --------------------------------------------------------------- purchase
def _quote_landed(q: Quote, qty: int) -> int:
    return landed_cents(q.unit_cents, qty, q.tax_bp, q.shipping_cents, q.fees_cents)


def _purchase(p: PurchaseProposal, cfg: BaseConfig, limits: Limits, quotes: dict,
              state: State, now: date, key: bytes, request: Optional[Request] = None) -> Decision:
    if request is not None:
        if p.item_id != request.item_id:
            return deny("item_not_requested")
        if p.qty != request.qty:
            return deny("qty_not_requested")
    item = cfg.items.get(p.item_id)
    if item is None:
        return deny("item_not_on_list")
    if not isinstance(p.qty, int) or isinstance(p.qty, bool) or p.qty < 1:
        return deny("bad_qty")
    if p.qty + state.ordered_qty.get(item.item_id, 0) > item.qty:
        return deny("qty_exceeds_list")
    q = quotes.get(p.quote_id)
    if q is None:
        return deny("unknown_quote")
    if q.item_id != item.item_id:
        return deny("quote_item_mismatch")
    if q.vendor not in cfg.vendors:
        return deny("vendor_not_approved")
    if q.currency != cfg.currency:
        return deny("currency_mismatch")
    if now > q.expires:
        return deny("quote_expired")

    landed = _quote_landed(q, p.qty)

    b = cfg.budget
    if not (b.period_start <= now <= b.period_end):
        return deny("budget_period_inactive")
    if state.spent_period_cents + landed > b.ceiling_cents:
        return deny("budget_exceeded")          # absolute: no approval can override

    level, reasons = "A", []

    def raise_to(lv: str, why: str) -> None:
        nonlocal level
        if LEVEL_ORDER[lv] > 0:
            reasons.append(why)
        level = max_level(level, lv)

    raise_to(amount_level(landed, limits), "amount_tier")
    cum = state.cum_item_window.get(item.item_id, 0) + landed
    raise_to(tier(cum, limits), "cumulative_item_window")
    if state.window_total_cents + landed > limits.window_cap_cents:
        raise_to("C", "window_cap")
    if item.category in limits.c_categories:
        raise_to("C", "category_requires_approval")

    margin = timedelta(days=limits.delivery_margin_days)

    def meets_nlt(x: Quote) -> bool:
        return now + timedelta(days=x.delivery_days) + margin <= item.nlt

    if not meets_nlt(q):
        raise_to("C", "delivery_infeasible")
    else:
        feasible = [(x, _quote_landed(x, p.qty)) for x in quotes.values()
                    if x.item_id == item.item_id and x.vendor in cfg.vendors
                    and x.currency == cfg.currency and now <= x.expires and meets_nlt(x)]
        best, best_cost = max(feasible, key=lambda t: (t[0].reliability_bp, -t[1]))
        same_rank = (q.reliability_bp, landed) == (best.reliability_bp, best_cost)
        alt_ok = (q.reliability_bp >= best.reliability_bp - limits.delta_bp
                  and landed * 10_000 <= best_cost * (10_000 - limits.gamma_bp))
        if not (same_rank or alt_ok):
            raise_to("C", "vendor_rule")

    def match(t: dict) -> bool:
        return (t["item_id"] == item.item_id and t["vendor"] == q.vendor
                and t["qty"] == p.qty and landed <= t["max_landed_cents"])

    return _finish(level, reasons, landed, p, state, key, now, "purchase", match)


# --------------------------------------------------------------- transfer
def _transfer(p: TransferProposal, cfg: BaseConfig, limits: Limits, quotes: dict,
              state: State, now: date, key: bytes) -> Decision:
    if p.from_acct == p.to_acct:
        return deny("same_account")
    if p.from_acct not in cfg.accounts or p.to_acct not in cfg.accounts:
        return deny("account_not_whitelisted")
    if not isinstance(p.amount_cents, int) or isinstance(p.amount_cents, bool) \
            or p.amount_cents < 1:
        return deny("bad_amount")
    q = quotes.get(p.for_quote_id)
    if q is None:
        return deny("unknown_quote")
    need = _quote_landed(q, p.for_qty)
    shortfall = max(0, need - state.balances.get(p.to_acct, 0))
    if p.amount_cents > shortfall:
        return deny("exceeds_documented_shortfall")
    if state.balances.get(p.from_acct, 0) < p.amount_cents:
        return deny("insufficient_funds")

    level, reasons = "A", []
    lv = amount_level(p.amount_cents, limits)
    if LEVEL_ORDER[lv] > 0:
        reasons.append("amount_tier")
    level = max_level(level, lv)
    if state.transfer_window_total_cents + p.amount_cents > limits.window_cap_cents:
        level = "C"
        reasons.append("transfer_window_cap")

    def match(t: dict) -> bool:
        return (t["from"] == p.from_acct and t["to"] == p.to_acct
                and p.amount_cents <= t["amount_cents"])

    return _finish(level, reasons, p.amount_cents, p, state, key, now, "transfer", match)


def evaluate(proposal, cfg: BaseConfig, limits: Limits, quotes: dict,
             state: State, now: date, key: bytes, request: Optional[Request] = None) -> Decision:
    """Pure function: same inputs, same Decision. Never raises."""
    try:
        if isinstance(proposal, PurchaseProposal):
            return _purchase(proposal, cfg, limits, quotes, state, now, key, request)
        if isinstance(proposal, TransferProposal):
            return _transfer(proposal, cfg, limits, quotes, state, now, key)
        return deny("unknown_proposal_type")
    except Exception as e:  # fail closed
        return deny(f"gate_error:{type(e).__name__}")


# ------------------------------------------------------------------- Gate
class Gate:
    def __init__(self, cfg: BaseConfig, packs: list, ledger: Ledger, key: bytes):
        self.cfg, self.ledger, self._key = cfg, ledger, key
        self.packs = list(packs)
        self.limits = compose(cfg.limits, self.packs)

    def decide(self, proposal, quotes: dict, now: date, request: Optional[Request] = None) -> Decision:
        """Read-only evaluation."""
        state = self.ledger.snapshot(self.cfg, self.limits, now)
        return evaluate(proposal, self.cfg, self.limits, quotes, state, now, self._key, request)

    def decide_and_commit(self, proposal, quotes: dict, now: date, request: Optional[Request] = None) -> Decision:
        """Evaluate inside one transaction; log every decision; record executed actions."""
        with self.ledger.tx():
            d = self.decide(proposal, quotes, now, request)
            self.ledger.append_log(now, "decision", {
                "proposal": type(proposal).__name__, "p": proposal.__dict__,
                "verdict": d.verdict, "level": d.level, "landed": d.landed_cents,
                "reasons": list(d.reasons)})
            if d.executes:
                if isinstance(proposal, PurchaseProposal):
                    q = quotes[proposal.quote_id]
                    self.ledger.record_order(now, proposal.item_id, q.vendor,
                                             proposal.qty, d.landed_cents, d.level,
                                             d.approval_id)
                else:
                    self.ledger.record_transfer(now, proposal.from_acct, proposal.to_acct,
                                                proposal.amount_cents, d.level, d.approval_id)
                if d.approval_id:
                    self.ledger.mark_approval_used(d.approval_id)
        return d
