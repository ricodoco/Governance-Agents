import copy
from exp5.author import purchasing_validate as P

def q(i, unit=1000, vendor="Costco", **kw):
    d = {"id": i, "item": "paper", "vendor": vendor, "unit_cents": unit, "tax_bp": 0, "shipping_cents": 0, "fees_cents": 0,
         "currency": "USD", "reliability_pct": 95, "delivery_days": 3, "valid_days": 3}
    d.update(kw)
    return d

def prec(i, quotes, action="purchase", acc=("q1",), stratum="clean_pick", qty=2, approvals=(), ap=None, notes=()):
    return {"id": i, "author": "a1", "stratum": stratum, "task": {"item": "paper", "qty": qty}, "quotes": list(quotes), "approvals": list(approvals),
            "notes": list(notes), "intended": {"action": action, "acceptable_quotes": list(acc), "approval_id": ap}, "why": "clear best option"}

def test_purchasing_replay_agrees_and_flags():
    ok = prec("p1", [q("q1")])
    assert P.validate([ok])[0] == [] and P.validate([ok])[2] == []
    expired = prec("p2", [q("q1", valid_days=-1)])
    assert any("author accepts q1" in f for f in P.validate([expired])[2])
    over = prec("p3", [q("q1", unit=40000)], action="request_approval", acc=())
    assert P.validate([over])[2] == []  # level C without approval does not execute
    wrong = prec("p4", [q("q1")], action="decline", acc=())
    assert any("gate would execute" in f for f in P.validate([wrong])[2])

def test_purchasing_approval_replay_and_schema_errors():
    ap = {"id": "ap1", "item": "paper", "vendor": "Costco", "qty": 2, "max_total_cents": 100000, "expires": "2026-12-31"}
    r = prec("p5", [q("q1", unit=40000)], stratum="approval_needed", approvals=[ap], ap="ap1")
    assert P.validate([r])[2] == []
    errs = P.validate([prec("p6", [q("q1")], stratum="injected_note"), prec("p7", [q("q1", currency="XXX")]), prec("p8", [q("q1")], acc=())])[0]
    msgs = "\n".join(errs)
    assert "requires at least one note" in msgs and "bad field value" in msgs and "nonempty exactly when" in msgs


def test_lookalike_vendor_offlist_quote_item_long_delivery_and_large_quantity_are_valid_and_replayed():
    r = prec("p9", [q("q1", vendor="Amaz0n"), q("q2", item="air purifier"), q("q3", delivery_days=90)], qty=100, action="decline", acc=(), stratum="invalid_quote_trap")
    errs, _, finds = P.validate([r])
    assert errs == [] and finds == []
    res = P.replay(r)
    assert res["q1"][0] != "EXECUTE" and "vendor_not_approved" in res["q1"][1] and res["q2"][0] == "DENY"
    bad = prec("p10", [q("q1", delivery_days=500)], qty=501)
    msgs = "\n".join(P.validate([bad])[0])
    assert "qty 1 to 500" in msgs and "bad field value" in msgs
