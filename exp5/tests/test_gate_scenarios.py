"""Scenario tests, named after the R-items in spec v0.2."""
import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from exp5.gate import (DENY, EXECUTE, EXECUTE_AND_NOTIFY, EXECUTE_APPROVED, REQUIRE_APPROVAL,
                       Limits, PurchaseProposal, TransferProposal, parse_pack)
from exp5.tests.helpers import (KEY, TODAY, approve, make_cfg, make_gate, make_quote, quotes_of)


def buy(gate, quotes, qty=1, item="paper", qid="q1", aid=None, commit=True, now=TODAY):
    p = PurchaseProposal(item, qid, qty, aid)
    return (gate.decide_and_commit if commit else gate.decide)(p, quotes, now)


# ---- tiers -----------------------------------------------------------------
def test_levels_a_b_c():
    g, _ = make_gate()
    assert buy(g, quotes_of(make_quote(unit=3000))).verdict == EXECUTE
    assert buy(g, quotes_of(make_quote(unit=20000))).verdict == EXECUTE_AND_NOTIFY
    d = buy(g, quotes_of(make_quote(unit=70000)))
    assert d.verdict == REQUIRE_APPROVAL and d.level == "C"


def test_exact_boundaries():
    g, _ = make_gate()
    assert buy(g, quotes_of(make_quote(unit=5000)), commit=False).level == "A"
    assert buy(g, quotes_of(make_quote(unit=5001)), commit=False).level == "B"
    assert buy(g, quotes_of(make_quote(unit=50000)), commit=False).level == "B"
    assert buy(g, quotes_of(make_quote(unit=50001)), commit=False).level == "C"


def test_R20_small_purchase_does_not_escalate():
    g, _ = make_gate()
    d = buy(g, quotes_of(make_quote(unit=2000)))
    assert d.verdict == EXECUTE and d.reasons == ()


def test_R8_threshold_hugging_escalates():
    g, _ = make_gate(make_cfg(Limits()))     # default hug margin on
    assert buy(g, quotes_of(make_quote(unit=4999)), commit=False).verdict == EXECUTE_AND_NOTIFY
    assert buy(g, quotes_of(make_quote(unit=49999)), commit=False).verdict == REQUIRE_APPROVAL


def test_fees_and_tax_count_toward_the_tier():
    g, _ = make_gate()
    q = make_quote(unit=4500, tax_bp=1000, ship=500)   # 4500+450+500 = 5450 -> B
    d = buy(g, quotes_of(q), commit=False)
    assert d.landed_cents == 5450 and d.level == "B"


# ---- R1 splitting ------------------------------------------------------------
def test_R1_splitting_is_caught_by_cumulative_window():
    g, led = make_gate()
    verdicts = []
    for i in range(3):
        verdicts.append(buy(g, quotes_of(make_quote(f"q{i}", "drill", unit=23300)),
                            item="drill", qid=f"q{i}").verdict)
    assert verdicts == [EXECUTE_AND_NOTIFY, EXECUTE_AND_NOTIFY, REQUIRE_APPROVAL]


def test_splitting_outside_the_window_is_not_cumulative():
    g, _ = make_gate()
    q = make_quote("q1", "drill", unit=40000)
    assert buy(g, quotes_of(q), item="drill", now=date(2026, 10, 12)).verdict == EXECUTE_AND_NOTIFY
    d = buy(g, quotes_of(q), item="drill", now=date(2026, 10, 19))   # 7 days later
    assert d.verdict == EXECUTE_AND_NOTIFY


# ---- vendor rule (R2, R14) -----------------------------------------------------
def test_R2_cheap_unreliable_vendor_needs_approval():
    g, _ = make_gate()
    best = make_quote("qa", vendor="Amazon", unit=1000, rel=9800)
    cheap = make_quote("qw", vendor="Walmart", unit=700, rel=8000)
    d = buy(g, quotes_of(best, cheap), qid="qw", commit=False)
    assert d.verdict == REQUIRE_APPROVAL and "vendor_rule" in d.reasons
    assert buy(g, quotes_of(best, cheap), qid="qa", commit=False).verdict == EXECUTE


def test_alternate_vendor_allowed_when_nearly_as_reliable_and_much_cheaper():
    g, _ = make_gate()
    best = make_quote("qa", vendor="Amazon", unit=1000, rel=9800)
    alt = make_quote("qw", vendor="Walmart", unit=800, rel=9700)     # -20%, -1 pt
    assert buy(g, quotes_of(best, alt), qid="qw", commit=False).verdict == EXECUTE
    meh = make_quote("qm", vendor="Costco", unit=900, rel=9700)      # only -10%
    assert buy(g, quotes_of(best, meh), qid="qm", commit=False).verdict == REQUIRE_APPROVAL


# ---- approvals (R4, R5-adjacent, R11, R12, R19) ---------------------------------
def test_C_executes_with_valid_matching_approval_and_is_single_use():
    g, led = make_gate()
    qs = quotes_of(make_quote(unit=70000))
    approve(led, "ap1", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=70000)
    d = buy(g, qs, aid="ap1")
    assert d.verdict == EXECUTE_APPROVED and d.notify
    again = buy(g, qs, aid="ap1")
    assert again.verdict == REQUIRE_APPROVAL and "approval_already_used" in again.reasons


def test_R4_approval_cannot_be_reused_for_changed_terms():
    g, led = make_gate()
    approve(led, "ap1", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=70000)
    # quantity changed
    d = buy(g, quotes_of(make_quote(unit=70000)), qty=2, aid="ap1", commit=False)
    assert d.verdict == REQUIRE_APPROVAL and "approval_terms_mismatch" in d.reasons
    # price rose above the approved maximum
    d = buy(g, quotes_of(make_quote(unit=70001)), aid="ap1", commit=False)
    assert "approval_terms_mismatch" in d.reasons
    # vendor changed
    d = buy(g, quotes_of(make_quote(vendor="Costco", unit=70000)), aid="ap1", commit=False)
    assert "approval_terms_mismatch" in d.reasons


def test_R12_forged_approval_rejected():
    g, led = make_gate()
    led.db.execute("INSERT INTO approvals(approval_id,terms,sig) VALUES('fake',?,?)",
                   ('{"kind":"purchase","item_id":"paper","vendor":"Amazon","qty":1,'
                    '"max_landed_cents":99999999,"expires":"2026-12-31"}', "00" * 32))
    d = buy(g, quotes_of(make_quote(unit=70000)), aid="fake", commit=False)
    assert d.verdict == REQUIRE_APPROVAL and "approval_bad_signature" in d.reasons


def test_approval_for_a_different_kind_or_expired_is_rejected():
    g, led = make_gate()
    approve(led, "tr", kind="transfer", **{"from": "checking", "to": "card", "amount_cents": 99999})
    assert "approval_wrong_kind" in buy(g, quotes_of(make_quote(unit=70000)), aid="tr",
                                        commit=False).reasons
    led.issue_approval(KEY, "old", {"kind": "purchase", "item_id": "paper", "vendor": "Amazon",
                                    "qty": 1, "max_landed_cents": 99999, "expires": "2026-10-01"})
    assert "approval_expired" in buy(g, quotes_of(make_quote(unit=70000)), aid="old",
                                     commit=False).reasons


def test_R19_owner_unreachable_means_nothing_executes():
    g, led = make_gate()
    d = buy(g, quotes_of(make_quote(unit=70000)))
    assert d.verdict == REQUIRE_APPROVAL
    assert led.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


# ---- hard rules (DENY) ----------------------------------------------------------
def test_item_not_on_list_and_wrong_item_quote():
    g, _ = make_gate()
    q = make_quote("qx", item="gadget")
    assert buy(g, quotes_of(q), item="gadget", qid="qx").reasons == ("item_not_on_list",)
    d = buy(g, quotes_of(make_quote("q1", item="paper")), item="drill", qid="q1")
    assert d.verdict == DENY and d.reasons == ("quote_item_mismatch",)   # R13


def test_unapproved_vendor_currency_and_stale_quote():
    g, _ = make_gate()
    assert buy(g, quotes_of(make_quote(vendor="ShadyShop"))).reasons == ("vendor_not_approved",)
    assert buy(g, quotes_of(make_quote(cur="EUR"))).reasons == ("currency_mismatch",)     # R16
    stale = make_quote(expires=date(2026, 10, 11))
    assert buy(g, quotes_of(stale)).reasons == ("quote_expired",)                         # R9


def test_quantity_cannot_exceed_owner_list_cumulatively():
    g, _ = make_gate()
    q = make_quote("q1", "drill", unit=1000)
    assert buy(g, quotes_of(q), qty=8, item="drill").executes
    d = buy(g, quotes_of(q), qty=3, item="drill")
    assert d.verdict == DENY and d.reasons == ("qty_exceeds_list",)


def test_R17_budget_period_rollover():
    g, _ = make_gate()
    q = make_quote(expires=date(2027, 2, 1))
    d = buy(g, quotes_of(q), now=date(2027, 1, 5))
    assert d.verdict == DENY and d.reasons == ("budget_period_inactive",)


def test_budget_is_absolute_even_with_a_valid_approval():
    g, led = make_gate(make_cfg(ceiling=100_000))
    approve(led, "ap1", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=70000)
    approve(led, "ap2", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=70000)
    qs = quotes_of(make_quote(unit=70000))
    assert buy(g, qs, aid="ap1").verdict == EXECUTE_APPROVED
    d = buy(g, qs, aid="ap2")
    assert d.verdict == DENY and d.reasons == ("budget_exceeded",)


def test_bad_qty_and_unknown_proposal_fail_closed():
    g, _ = make_gate()
    assert buy(g, quotes_of(make_quote()), qty="3", commit=False).verdict == DENY
    assert buy(g, quotes_of(make_quote()), qty=0, commit=False).verdict == DENY
    assert g.decide("buy everything", {}, TODAY).verdict == DENY


# ---- delivery (R18) and categories ------------------------------------------------
def test_R18_no_vendor_meets_deadline_escalates():
    g, _ = make_gate()
    slow = make_quote("q1", "tile", unit=2000, days=10)      # nlt Oct 20, today Oct 12
    d = buy(g, quotes_of(slow), item="tile", commit=False)
    assert d.verdict == REQUIRE_APPROVAL and "delivery_infeasible" in d.reasons


def test_choosing_a_slow_vendor_when_a_fast_one_exists_escalates():
    g, _ = make_gate()
    slow = make_quote("qs", "tile", vendor="Walmart", unit=1000, rel=9900, days=10)
    fast = make_quote("qf", "tile", vendor="HomeDepot", unit=1200, rel=9000, days=2)
    assert "delivery_infeasible" in buy(g, quotes_of(slow, fast), item="tile", qid="qs",
                                        commit=False).reasons
    assert buy(g, quotes_of(slow, fast), item="tile", qid="qf", commit=False).verdict == EXECUTE


def test_pack_can_force_approval_for_a_category():
    p = parse_pack("name: repairs\nversion: 1.0.0\ndomain: home\nchangelog: x\n"
                   "rules:\n  c_categories: [home-repair]\n")
    g, _ = make_gate(packs=[p])
    d = buy(g, quotes_of(make_quote("q1", "tile", unit=1000, days=2)), item="tile", commit=False)
    assert d.verdict == REQUIRE_APPROVAL and "category_requires_approval" in d.reasons


def test_pack_cannot_loosen_so_gate_construction_fails():
    from exp5.gate import PackError
    p = parse_pack("name: loose\nversion: 1.0.0\ndomain: x\nchangelog: x\n"
                   "rules:\n  b_max_cents: 900000\n")
    with pytest.raises(PackError):
        make_gate(packs=[p])


def test_window_cap_escalates():
    g, _ = make_gate(make_cfg(Limits(hug_margin_bp=0, window_cap_cents=30_000)))
    assert buy(g, quotes_of(make_quote("q1", unit=20000))).verdict == EXECUTE_AND_NOTIFY
    d = buy(g, quotes_of(make_quote("q2", item="paper", unit=20000)), qid="q2")
    assert d.verdict == REQUIRE_APPROVAL and "window_cap" in d.reasons


# ---- transfers (D8, R6) -----------------------------------------------------------
def tr(g, amount, need_unit=30000, frm="checking", to="card", aid=None, commit=True):
    qs = quotes_of(make_quote("qn", unit=need_unit))
    p = TransferProposal(frm, to, amount, "qn", 1, aid)
    return (g.decide_and_commit if commit else g.decide)(p, qs, TODAY)


def test_transfer_limited_to_documented_shortfall():
    g, led = make_gate()
    d = tr(g, 30000)
    assert d.verdict == EXECUTE_AND_NOTIFY
    assert led.snapshot(g.cfg, g.limits, TODAY).balances["card"] == 30000
    assert tr(g, 1).reasons == ("exceeds_documented_shortfall",)     # need already covered


def test_transfer_over_shortfall_denied():
    g, _ = make_gate()
    assert tr(g, 30001).reasons == ("exceeds_documented_shortfall",)


def test_transfer_to_unlisted_account_and_same_account_denied():
    g, _ = make_gate()
    assert tr(g, 100, to="offshore").reasons == ("account_not_whitelisted",)
    assert tr(g, 100, frm="card", to="card").reasons == ("same_account",)


def test_transfer_cannot_overdraw_source():
    g, _ = make_gate(balances={"checking": 100, "card": 0})
    assert tr(g, 500).reasons == ("insufficient_funds",)


def test_R6_transfer_does_not_create_budget_headroom():
    cfg = make_cfg(ceiling=50_000)
    g, led = make_gate(cfg)
    tr(g, 30000)                                   # fund the card
    d = buy(g, quotes_of(make_quote(unit=60000)), commit=False)
    assert d.verdict == DENY and d.reasons == ("budget_exceeded",)


def test_large_transfer_needs_approval():
    g, led = make_gate()
    d = tr(g, 70000, need_unit=70000)
    assert d.verdict == REQUIRE_APPROVAL
    approve(led, "t1", kind="transfer", **{"from": "checking", "to": "card", "amount_cents": 70000})
    assert tr(g, 70000, need_unit=70000, aid="t1").verdict == EXECUTE_APPROVED


# ---- ledger and log ----------------------------------------------------------------
def test_every_decision_is_logged_and_chain_verifies():
    g, led = make_gate()
    buy(g, quotes_of(make_quote(unit=1000)))
    buy(g, quotes_of(make_quote(unit=70000)))
    buy(g, quotes_of(make_quote(vendor="ShadyShop")))
    kinds = [(k, p["verdict"]) for _, _, k, p in led.read_log()]
    assert kinds == [("decision", EXECUTE), ("decision", REQUIRE_APPROVAL), ("decision", DENY)]
    assert led.verify_chain()


def test_log_is_append_only_and_tampering_is_detected():
    g, led = make_gate()
    buy(g, quotes_of(make_quote(unit=1000)))
    with pytest.raises(sqlite3.Error):
        led.db.execute("UPDATE log SET kind='x'")
    with pytest.raises(sqlite3.Error):
        led.db.execute("DELETE FROM log")
    led.db.execute("DROP TRIGGER log_no_update")           # an attacker with raw DB access
    led.db.execute("UPDATE log SET payload='{}' WHERE seq=1")
    assert not led.verify_chain()


def test_denied_and_pending_decisions_record_no_orders():
    g, led = make_gate()
    buy(g, quotes_of(make_quote(unit=70000)))
    buy(g, quotes_of(make_quote(cur="EUR")))
    assert led.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
