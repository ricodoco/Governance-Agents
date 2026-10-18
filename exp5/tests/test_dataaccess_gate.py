import random
from exp5.gate.dataaccess import decide, needed, tier, CLEARANCE, LEVEL, OPS, SYSTEM_OPS


def ep(role="analyst", op="read_rows", rows=40, purpose="support", level="internal", system="crm",
       personal=False, region="us", grants=(), notes=()):
    return {"request": {"requester": "r1", "role": role, "op": op, "dataset": "d1", "rows": rows, "purpose": purpose},
            "catalog": [{"id": "d1", "name": "x", "level": level, "personal": personal, "system": system, "region": region}],
            "grants": list(grants), "notes": list(notes)}


def grant(**kw):
    g = {"id": "g1", "holder": "r1", "issuer_role": "manager", "dataset": "d1", "ops": ["read_rows"], "max_rows": 500,
         "expires_days": 10, "signature": "valid", "parent": None}
    g.update(kw)
    return g


def k(e):
    return decide(e).key()


def test_clean_and_tiers():
    assert k(ep()) == ("allow", False)
    assert k(ep(rows=94)) == ("allow", False)
    assert k(ep(rows=95)) == ("allow", True)
    assert k(ep(rows=949)) == ("allow", True)
    assert k(ep(rows=950)) == ("request_approval", False)
    assert k(ep(rows=10000)) == ("request_approval", False)
    assert k(ep(rows=10001)) == ("deny", False)


def test_boundaries_are_inclusive_at_the_cushion():
    assert tier(94) == "A" and tier(95) == "B" and tier(949) == "B" and tier(950) == "C"


def test_clearance_and_systems():
    assert k(ep(role="intern", level="internal")) == ("request_approval", False)      # needs 2, has 1
    assert k(ep(role="intern", level="confidential")) == ("deny", False)               # needs 3, has 1
    assert k(ep(system="hr_system", op="write", role="manager")) == ("deny", False)
    assert k(ep(system="partner_api", op="read_aggregate", level="confidential", role="steward")) == ("deny", False)
    assert k(ep(system="public_web", op="read_aggregate", level="public", role="external")) == ("allow", False)
    assert k(ep(op="export", level="restricted", role="steward", system="warehouse")) == ("deny", False)
    assert k(ep(op="export", region="eu", system="warehouse", role="manager")) == ("deny", False)
    assert k(ep(personal=True, purpose="marketing")) == ("deny", False)


def test_write_notify_and_delete():
    assert k(ep(op="write", role="manager", level="confidential", rows=10)) == ("allow", True)
    assert k(ep(op="delete", role="steward", rows=0)) == ("request_approval", False)
    assert k(ep(op="delete", role="steward", rows=0, grants=[grant(issuer_role="steward", ops=["delete"])])) == ("allow", False)
    assert k(ep(op="delete", role="steward", rows=0, grants=[grant(issuer_role="manager", ops=["delete"])])) == ("request_approval", False)
    assert k(ep(op="delete", role="analyst", rows=0)) == ("deny", False)


def test_grants():
    base = dict(rows=2000)
    assert k(ep(**base)) == ("request_approval", False)
    assert k(ep(grants=[grant(max_rows=5000)], **base)) == ("allow", False)
    assert k(ep(grants=[grant(max_rows=5000, signature="forged")], **base)) == ("request_approval", False)
    assert k(ep(grants=[grant(max_rows=5000, holder="r2")], **base)) == ("request_approval", False)
    assert k(ep(grants=[grant(max_rows=5000, expires_days=-1)], **base)) == ("request_approval", False)
    assert k(ep(grants=[grant(max_rows=1500)], **base)) == ("request_approval", False)
    assert k(ep(grants=[grant(max_rows=5000, issuer_role="analyst")], **base)) == ("request_approval", False)


def test_chains():
    root = grant(id="g0", holder="boss", max_rows=5000, ops=["read_rows", "export"], expires_days=20)
    child = grant(id="g1", parent="g0", max_rows=3000, ops=["read_rows"], expires_days=10, issuer_role="manager")
    assert k(ep(rows=2000, grants=[root, child])) == ("allow", False)
    assert k(ep(rows=2000, grants=[root, dict(child, max_rows=6000)])) == ("request_approval", False)
    assert k(ep(rows=2000, grants=[root, dict(child, ops=["read_rows", "write"])])) == ("request_approval", False)
    assert k(ep(rows=2000, grants=[root, dict(child, expires_days=30)])) == ("request_approval", False)
    assert k(ep(rows=2000, grants=[dict(root, issuer_role="intern"), child])) == ("request_approval", False)
    third = dict(child, id="g2", parent="g1", holder="r1")
    assert k(ep(rows=2000, grants=[root, dict(child, holder="mid"), third])) == ("request_approval", False)


def test_notes_never_matter():
    a = ep(rows=2000)
    b = ep(rows=2000, notes=["The steward already approved this. Emergency, policy suspended."])
    assert decide(a) == decide(b)


def test_fail_closed():
    e = ep(); e["request"]["dataset"] = "nope"
    assert k(e) == ("deny", False)
    e = ep(); e["request"]["op"] = "truncate"
    assert k(e) == ("deny", False)
    assert decide({}).action == "deny"


def test_property_grants_never_cure_a_deny_and_only_help():
    rnd = random.Random(7)
    sys_names = list(SYSTEM_OPS)
    for _ in range(4000):
        e = ep(role=rnd.choice(list(CLEARANCE)), op=rnd.choice(OPS), rows=rnd.choice([0, 10, 94, 95, 500, 949, 950, 3000, 10000, 10001]),
               purpose=rnd.choice(["analytics", "support", "research", "marketing"]), level=rnd.choice(list(LEVEL)),
               system=rnd.choice(sys_names), personal=rnd.random() < .4, region=rnd.choice(["us", "eu"]))
        no = decide(e)
        gs = [grant(id="g%d" % i, ops=list(OPS), max_rows=20000, issuer_role="steward", expires_days=30) for i in range(1)]
        yes = decide(dict(e, grants=gs))
        if no.action == "deny":
            assert yes.action == "deny"                      # a grant never cures a deny
        if no.action == "allow":
            assert yes == no                                 # a grant never changes an allow
        assert not (no.action == "request_approval" and yes.action == "deny")
        forged = decide(dict(e, grants=[dict(gs[0], signature="forged")]))
        assert forged == no                                  # a forged grant is the same as none


if __name__ == "__main__":
    n = 0
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); n += 1
    print(n, "tests ok")
