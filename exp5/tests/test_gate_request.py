"""Two gate rules added after the injection test: the principal's structured request binds item and quantity, and an approval id that nothing needs is denied."""
import pytest
from hypothesis import given, settings, strategies as st

from exp5.gate import (DENY, EXECUTE, EXECUTE_AND_NOTIFY, EXECUTE_APPROVED, REQUIRE_APPROVAL, PurchaseProposal, Request)
from exp5.tests.helpers import TODAY, approve, make_gate, make_quote, quotes_of


def go(g, qs, item="paper", qty=1, qid="q1", aid=None, req=None, commit=False):
    p = PurchaseProposal(item, qid, qty, aid)
    return (g.decide_and_commit if commit else g.decide)(p, qs, TODAY, req)


def test_without_a_request_nothing_changes_for_level_a_and_b_purchases():
    g, _ = make_gate()
    assert go(g, quotes_of(make_quote(unit=3000))).verdict == EXECUTE
    assert go(g, quotes_of(make_quote(unit=20000))).verdict == EXECUTE_AND_NOTIFY


def test_a_matching_request_changes_nothing():
    g, _ = make_gate()
    assert go(g, quotes_of(make_quote(unit=3000)), req=Request("paper", 1)).verdict == EXECUTE


def test_a_purchase_of_another_item_than_requested_is_denied_even_when_policy_allows_it():
    g, _ = make_gate()
    qs = quotes_of(make_quote(item="drill", unit=3000))
    assert go(g, qs, item="drill").verdict == EXECUTE                       # on the list, within limits: policy alone allows it
    d = go(g, qs, item="drill", req=Request("paper", 1))
    assert d.verdict == DENY and d.reasons == ("item_not_requested",)


def test_a_different_quantity_than_requested_is_denied():
    g, _ = make_gate()
    qs = quotes_of(make_quote(unit=1000))
    for qty in (2, 3):
        d = go(g, qs, qty=qty, req=Request("paper", 1))
        assert d.verdict == DENY and d.reasons == ("qty_not_requested",)
    assert go(g, qs, qty=2, req=Request("paper", 2)).verdict == EXECUTE


def test_a_denied_request_mismatch_commits_nothing():
    g, led = make_gate()
    qs = quotes_of(make_quote(item="drill", unit=3000))
    go(g, qs, item="drill", req=Request("paper", 1), commit=True)
    assert go(g, qs, item="drill").verdict == EXECUTE and led.snapshot(g.cfg, g.limits, TODAY).spent_period_cents == 0


@pytest.mark.parametrize("unit,level", [(3000, "A"), (20000, "B")])
def test_a_cited_approval_is_denied_at_levels_a_and_b_whether_or_not_it_exists(unit, level):
    g, led = make_gate()
    qs = quotes_of(make_quote(unit=unit))
    d = go(g, qs, aid="ap9")
    assert d.verdict == DENY and d.reasons == ("approval_not_needed",)
    approve(led, "ap1", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=unit)
    d = go(g, qs, aid="ap1")
    assert d.verdict == DENY and d.reasons == ("approval_not_needed",)
    assert go(g, qs).level == level


def test_level_c_still_executes_with_a_real_approval_and_still_asks_for_one_when_the_cited_id_is_unknown():
    g, led = make_gate()
    qs = quotes_of(make_quote(unit=70000))
    approve(led, "ap1", item_id="paper", vendor="Amazon", qty=1, max_landed_cents=70000)
    assert go(g, qs, aid="ap1", req=Request("paper", 1)).verdict == EXECUTE_APPROVED
    d = go(g, qs, aid="ap9", req=Request("paper", 1))
    assert d.verdict == REQUIRE_APPROVAL and "approval_unknown" in d.reasons


@settings(max_examples=60, deadline=None)
@given(item=st.sampled_from(["paper", "drill", "tile"]), qty=st.integers(1, 3), unit=st.sampled_from([500, 3000, 20000, 70000]),
       aid=st.sampled_from([None, "ap9"]), req_item=st.sampled_from(["paper", "drill", "tile"]), req_qty=st.integers(1, 3))
def test_with_a_request_an_executed_purchase_is_always_the_requested_item_and_quantity_and_cites_no_unneeded_approval(item, qty, unit, aid, req_item, req_qty):
    g, _ = make_gate()
    qs = quotes_of(make_quote(item=item, unit=unit))
    d = go(g, qs, item=item, qty=qty, aid=aid, req=Request(req_item, req_qty))
    if d.executes:
        assert item == req_item and qty == req_qty
        assert aid is None or d.level == "C"
