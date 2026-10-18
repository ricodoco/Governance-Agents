import random

import pytest

from exp5.sim import runner
from exp5.sim.runner import run_all, run_scenario
from exp5.sim.scenarios import FAMILIES, build
from exp5.sim.world import make_market

N = 30
ADVERSARIAL = [f for f, (_, k, _) in FAMILIES.items() if k == "adversarial"]
BENIGN = [f for f, (_, k, _) in FAMILIES.items() if k == "benign"]


def test_vendor_quote_uses_observed_history_not_claims():
    m = make_market(random.Random(7), inflate_bp=2000)
    for v in m.values():
        q = v.quote("q", "paper", 1000, 1)
        assert q.reliability_bp == v.observed_bp
        assert v.claimed_bp >= v.observed_bp          # the claim is inflated and ignored


def test_scenarios_are_deterministic_and_split_is_30_percent_heldout():
    for f in FAMILIES:
        a, b = build(f, 5, seed=3), build(f, 5, seed=3)
        assert [s.quotes for s in a.steps] == [s.quotes for s in b.steps]
        assert [s.proposal for s in a.steps] == [s.proposal for s in b.steps]
    held = sum(build("R1_split", v).split == "heldout" for v in range(100))
    assert held == 30


@pytest.fixture(scope="module")
def rows():
    return run_all(N, seed=1)


def test_gate_arm_has_zero_unsafe_false_alarm_or_mismatch(rows):
    bad = [r for r in rows if r["arm"] == "gate" and r["kind"] != "ok"]
    assert bad == [], bad[:3]


def test_naive_arm_is_actually_exposed_so_scenarios_are_not_vacuous(rows):
    for f in ADVERSARIAL:
        u = sum(r["kind"] == "unsafe_execution" for r in rows if r["arm"] == "naive" and r["family"] == f)
        assert u > 0, f"{f}: naive arm never fails, scenario proves nothing"
    for f in BENIGN:
        fa = sum(r["kind"] == "false_alarm" for r in rows if r["arm"] == "naive" and r["family"] == f)
        assert fa == 0


def _unsafe(family, n=20):
    return sum(run_scenario(build(family, v), "gate")["kind"] == "unsafe_execution"
               for v in range(n))


def test_scenarios_detect_a_broken_signature_check(monkeypatch):
    assert _unsafe("R12_forged") == 0
    monkeypatch.setattr("exp5.gate.gate.signature_ok", lambda *a: True)
    assert _unsafe("R12_forged") == 20


def test_scenarios_detect_a_broken_hugging_rule(monkeypatch):
    assert _unsafe("R8_hugging") == 0
    monkeypatch.setattr("exp5.gate.gate._hugging", lambda *a: False)
    assert _unsafe("R8_hugging") == 20


def test_scenarios_detect_ignored_approval_terms(monkeypatch):
    assert _unsafe("R4_reuse") == 0
    monkeypatch.setattr("exp5.gate.gate._approval_check",
                        lambda p, state, key, now, kind, match:
                        (True, "") if p.approval_id in state.approvals else (False, "x"))
    assert _unsafe("R4_reuse") == 20
