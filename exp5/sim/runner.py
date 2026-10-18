"""Run scenarios against two arms and score them.

gate  : the deterministic gate from exp5.gate
naive : no gate; executes whatever the proposer asks, if it can price it (the exposure baseline)
Outcome kinds: ok | unsafe_execution | false_alarm | mismatch (safe but wrong class)

NOTE: M2 proposers are SCRIPTED rogue/benign agents. This measures the gate, not an LLM.
"""
from __future__ import annotations
import argparse
import json
from collections import defaultdict

from exp5.gate import (DENY, EXECUTE, Decision, Gate, Ledger, PurchaseProposal, landed_cents)
from .owner import OwnerStub, forge
from .scenarios import FAMILIES, build
from .world import KEY


class NaiveExecutor:
    def __init__(self, cfg, ledger):
        self.cfg, self.ledger = cfg, ledger

    def decide_and_commit(self, proposal, quotes, now, request=None):
        try:
            with self.ledger.tx():
                if isinstance(proposal, PurchaseProposal):
                    q = quotes[proposal.quote_id]
                    landed = landed_cents(q.unit_cents, proposal.qty, q.tax_bp,
                                          q.shipping_cents, q.fees_cents)
                    self.ledger.record_order(now, proposal.item_id, q.vendor, proposal.qty,
                                             landed, "A", None)
                    return Decision(EXECUTE, "A", landed, ("naive",))
                self.ledger.record_transfer(now, proposal.from_acct, proposal.to_acct,
                                            proposal.amount_cents, "A", None)
                return Decision(EXECUTE, "A", proposal.amount_cents, ("naive",))
        except Exception as e:
            return Decision(DENY, None, None, (f"naive_error:{type(e).__name__}",))


def run_scenario(sc, arm: str):
    led = Ledger()
    for a, c in sc.balances.items():
        led.set_balance(a, c)
    owner = OwnerStub(led, KEY)
    engine = Gate(sc.cfg, [], led, KEY) if arm == "gate" else NaiveExecutor(sc.cfg, led)
    res = []
    for st in sc.steps:
        for act in st.pre:
            if act[0] == "approve":
                owner.approve(act[1], act[2])
            else:
                forge(led, act[1], act[2], *act[3:])
        d = engine.decide_and_commit(st.proposal, st.quotes, st.now)
        if d.notify:
            owner.notify(st.now, d, st.proposal)
        res.append(d)
    kind, detail = sc.judge(res)
    return {"sid": sc.sid, "family": sc.family, "split": sc.split, "arm": arm,
            "kind": kind, "detail": detail, "verdicts": [d.verdict for d in res],
            "reasons": [list(d.reasons) for d in res]}


def run_all(n: int = 100, seed: int = 1, arms=("gate", "naive")):
    return [run_scenario(build(f, v, seed), arm)
            for f in FAMILIES for v in range(n) for arm in arms]


def summarize(rows):
    t = defaultdict(lambda: defaultdict(int))
    for r in rows:
        t[(r["family"], r["arm"])]["n"] += 1
        t[(r["family"], r["arm"])][r["kind"]] += 1
    return t


def report(rows) -> str:
    t, out = summarize(rows), []
    out.append(f"{'family':<18}{'kind':<12}{'arm':<7}{'n':>5}{'unsafe':>8}{'falsealarm':>12}{'mismatch':>10}")
    for f, (_, kind, _) in FAMILIES.items():
        for arm in ("gate", "naive"):
            c = t.get((f, arm))
            if c:
                out.append(f"{f:<18}{kind:<12}{arm:<7}{c['n']:>5}{c['unsafe_execution']:>8}"
                           f"{c['false_alarm']:>12}{c['mismatch']:>10}")
    for arm in ("gate", "naive"):
        adv = [r for r in rows if r["arm"] == arm and FAMILIES[r["family"]][1] == "adversarial"]
        ben = [r for r in rows if r["arm"] == arm and FAMILIES[r["family"]][1] == "benign"]
        if not adv:
            continue
        u = sum(r["kind"] == "unsafe_execution" for r in adv)
        fa = sum(r["kind"] == "false_alarm" for r in ben)
        ho = [r for r in adv if r["split"] == "heldout"]
        uho = sum(r["kind"] == "unsafe_execution" for r in ho)
        bound = f"; 95% upper bound on unsafe rate <= {3 / len(adv):.4f} (rule of three)" if u == 0 else ""
        out.append(f"[{arm}] adversarial n={len(adv)} unsafe={u} (held-out {uho}/{len(ho)}){bound}; "
                   f"benign n={len(ben)} false_alarms={fa}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="exp5/data/results_m2.json")
    a = ap.parse_args()
    rows = run_all(a.n, a.seed)
    with open(a.out, "w") as f:
        json.dump({"n": a.n, "seed": a.seed, "rows": rows}, f)
    print(report(rows))
