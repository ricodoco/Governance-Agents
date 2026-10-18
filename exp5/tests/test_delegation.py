from datetime import date

from exp5.sim.delegation import (FAMILIES, PROFILES, ROOT_KEY, _amount, _cav, amounts_for_b, attenuate, decide,
                                 intersect, issue, issue_ceilings, make_case, run_part_a, verify)
from exp5.sim.world import TODAY

HAZ, BEN = amounts_for_b(step=10)
ROOT = {"max_cents": 50_000, "items": ("paper", "drill"), "expires": "2027-01-01", "depth": 2}
NOW = date(2026, 10, 12)


def test_valid_chain_verifies_and_returns_the_last_links_caveats():
    chain = attenuate(issue("root", ROOT), "d", {**ROOT, "max_cents": 1000, "depth": 1})
    ok, why, cav = verify(chain, NOW)
    assert ok and cav["max_cents"] == 1000


def test_tampering_widening_depth_and_expiry_are_all_refused():
    base = issue("root", ROOT)
    good = attenuate(base, "d", {**ROOT, "depth": 1})
    h, c, s = good[-1]
    assert verify([good[0], (h, {**c, "max_cents": 49_999}, s)], NOW)[1] == "bad_signature"
    assert verify(attenuate(base, "d", {**ROOT, "max_cents": 60_000, "depth": 1}), NOW)[1] == "not_attenuated"
    assert verify(attenuate(base, "d", {**ROOT, "items": ("paper", "ladder"), "depth": 1}), NOW)[1] == "not_attenuated"
    assert verify(attenuate(attenuate(base, "m", {**ROOT, "depth": 0}), "x", {**ROOT, "depth": 0}), NOW)[1] == "not_attenuated"
    assert verify(good, date(2027, 6, 1))[1] == "expired"
    assert verify([], NOW)[1] == "no_token"


def test_intersection_takes_the_most_restrictive_value_of_every_caveat():
    e = intersect([ROOT, {**ROOT, "max_cents": 900, "items": ("paper",), "depth": 1}])
    assert e["max_cents"] == 900 and e["items"] == ("paper",) and e["depth"] == 1


def test_a_naive_delegate_is_unsafe_on_every_adversarial_family():
    res = run_part_a(40)
    for fam in FAMILIES:
        if fam[0] == "B" or fam.startswith("C2"):
            assert res[("naive", fam)]["unsafe"] == 40, fam


def test_token_arm_blocks_every_delegation_attack_except_loss_of_contact():
    res = run_part_a(40)
    for fam in FAMILIES:
        r = res[("tokens", fam)]
        assert r["unsafe"] == (40 if fam.startswith("C2") else 0), fam
        assert r["false_alarm"] == 0, fam


def test_full_arm_has_no_unsafe_execution_and_no_false_alarm():
    res = run_part_a(100)
    assert sum(r["unsafe"] for (arm, f), r in res.items() if arm == "full") == 0
    assert sum(r["false_alarm"] for (arm, f), r in res.items() if arm == "full") == 0


def test_a_forged_token_and_bare_text_never_execute_under_verification():
    for arm in ("tokens", "full"):
        for fam in ("B4_forged", "B7_spoofed_text"):
            assert not any(decide(make_case(fam, v), arm)[0] for v in range(40))


def test_certificate_derived_ceilings_cut_loss_and_respect_the_root_budget():
    rows = {r["delegate"]: r for r in issue_ceilings(PROFILES, HAZ, BEN)}
    assert all(r["true_loss_cert"] <= r["true_loss_uniform"] + 1e-9 for r in rows.values())
    assert rows["sparse"]["ceiling"] < rows["strong"]["ceiling"]        # thin evidence earns less authority
    assert rows["poor"]["ceiling"] < rows["strong"]["ceiling"]
    assert sum(r["cert_loss_ub"] for r in rows.values()) <= 60.0        # bounds add up under the root budget


def test_a_certificate_cannot_see_an_unrepresentative_probe_set():
    h = {r["delegate"]: r for r in issue_ceilings(PROFILES, HAZ, BEN)}["hidden"]
    assert h["ceiling"] == 50_000 and h["true_loss_cert"] > h["share_budget"]   # the stated limit
