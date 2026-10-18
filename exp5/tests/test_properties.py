"""Property tests: the guarantees must hold for ALL generated inputs, not just examples."""
from datetime import date, timedelta

from hypothesis import given, settings, strategies as st

from exp5.gate import (Budget, Limits, PurchaseProposal, State, TransferProposal, evaluate)
from exp5.tests.helpers import KEY, TODAY, approve, make_cfg, make_gate, make_quote, quotes_of

SETTINGS = dict(max_examples=150, deadline=None)


@settings(**SETTINGS)
@given(steps=st.lists(st.tuples(st.integers(1, 120_000), st.integers(1, 3),
                                st.sampled_from(["Amazon", "Costco", "Walmart"]),
                                st.integers(0, 40)), min_size=1, max_size=25),
       ceiling=st.integers(10_000, 400_000))
def test_budget_is_never_exceeded_even_if_owner_approves_everything(steps, ceiling):
    g, led = make_gate(make_cfg(ceiling=ceiling))
    for i, (unit, qty, vendor, day_off) in enumerate(steps):
        now = TODAY + timedelta(days=day_off)
        q = make_quote(f"q{i}", "paper", vendor, unit)
        approve(led, f"a{i}", item_id="paper", vendor=vendor, qty=qty, max_landed_cents=10**9)
        g.decide_and_commit(PurchaseProposal("paper", f"q{i}", qty, f"a{i}"), quotes_of(q), now)
    spent = led.db.execute("SELECT COALESCE(SUM(landed_cents),0) FROM orders").fetchone()[0]
    assert spent <= ceiling


@settings(**SETTINGS)
@given(steps=st.lists(st.tuples(st.integers(1, 90_000), st.integers(1, 3)),
                      min_size=1, max_size=30))
def test_without_approvals_executed_spend_per_item_stays_within_level_B(steps):
    """Splitting can never smuggle more than b_max per item past the owner in one window."""
    limits = Limits(hug_margin_bp=0)
    g, led = make_gate(make_cfg(limits, ceiling=10**9))
    for i, (unit, qty) in enumerate(steps):
        q = make_quote(f"q{i}", "paper", "Amazon", unit)
        g.decide_and_commit(PurchaseProposal("paper", f"q{i}", qty), quotes_of(q), TODAY)
    per_item, total, high = led.db.execute(
        "SELECT COALESCE(SUM(landed_cents),0), COALESCE(SUM(landed_cents),0), "
        "COALESCE(SUM(level='C'),0) FROM orders").fetchone()
    assert per_item <= limits.b_max_cents
    assert total <= limits.window_cap_cents
    assert high == 0


listed = st.sampled_from(["paper", "drill", "tile", "ghost"])
vendors = st.sampled_from(["Amazon", "Costco", "Walmart", "HomeDepot", "ShadyShop"])


@settings(max_examples=400, deadline=None)
@given(item=listed, qitem=listed, vendor=vendors, cur=st.sampled_from(["USD", "EUR"]),
       unit=st.integers(0, 200_000), qty=st.integers(-1, 5), tax=st.integers(0, 3000),
       ship=st.integers(0, 5000), spent=st.integers(0, 600_000),
       exp_off=st.integers(-3, 3), approve_all=st.booleans())
def test_anything_that_executes_satisfies_the_hard_rules(item, qitem, vendor, cur, unit, qty,
                                                         tax, ship, spent, exp_off, approve_all):
    cfg = make_cfg(ceiling=500_000)
    q = make_quote("q1", qitem, vendor, unit, tax_bp=tax, ship=ship, cur=cur,
                   expires=TODAY + timedelta(days=exp_off))
    state = State(spent_period_cents=spent)
    if approve_all:
        from exp5.gate.approvals import sign_terms
        from exp5.gate.models import Approval
        t = {"kind": "purchase", "item_id": item, "vendor": vendor, "qty": qty,
             "max_landed_cents": 10**9, "expires": "2026-12-31"}
        state = State(spent_period_cents=spent,
                      approvals={"ap": Approval("ap", t, sign_terms(KEY, t))})
    d = evaluate(PurchaseProposal(item, "q1", qty, "ap" if approve_all else None),
                 cfg, cfg.limits, {"q1": q}, state, TODAY, KEY)
    assert evaluate(PurchaseProposal(item, "q1", qty, "ap" if approve_all else None),
                    cfg, cfg.limits, {"q1": q}, state, TODAY, KEY) == d     # deterministic
    if d.executes:
        assert item in cfg.items and qitem == item
        assert vendor in cfg.vendors and cur == "USD"
        assert q.expires >= TODAY
        assert isinstance(qty, int) and qty >= 1
        assert spent + d.landed_cents <= cfg.budget.ceiling_cents


@settings(max_examples=300, deadline=None)
@given(amount=st.integers(-5, 100_000), need=st.integers(0, 100_000),
       bal_src=st.integers(0, 150_000), bal_dst=st.integers(0, 100_000),
       frm=st.sampled_from(["checking", "card", "offshore"]),
       to=st.sampled_from(["checking", "card", "offshore"]))
def test_transfers_never_exceed_shortfall_or_leave_the_whitelist(amount, need, bal_src,
                                                                  bal_dst, frm, to):
    cfg = make_cfg()
    q = make_quote("q1", unit=max(need, 0))
    state = State(balances={"checking": 0, "card": 0, "offshore": 0, frm: bal_src, **{to: bal_dst}}
                  if frm != to else {frm: bal_src})
    d = evaluate(TransferProposal(frm, to, amount, "q1", 1), cfg, cfg.limits,
                 {"q1": q}, state, TODAY, KEY)
    if d.executes:
        assert frm != to and {frm, to} <= cfg.accounts
        assert 0 < amount <= max(0, need - state.balances.get(to, 0))
        assert amount <= state.balances[frm]
