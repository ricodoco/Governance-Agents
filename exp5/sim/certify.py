"""Week 2 experiment: coverage estimator accuracy, then a 12-month drift test of the calibrator.

  python -m exp5.sim.certify [--results exp5/data/results_m5b_n100.json --arm annotated+primed]
Without --results it uses the counts reported for annotated+primed in M4 (seen 5/150, withheld 50/60,
benign refusals 24/120). Every number below the estimator table comes from a simulation whose true
parameters are printed, so the loop is tested, not the world.
"""
from __future__ import annotations
import argparse
import json
import random
import statistics as st

from exp5.gate import PurchaseProposal, landed_cents
from .calibrator import (BASE_CEILING, Business, Evidence, cp_upper, choose_ceiling, expected_loss,
                         review_cost)
from .coverage import (COMPOUND, UNTESTED_M2, FAMILIES, M3_FAMILIES, build_any, calibrate,
                       estimate_uncovered_share, sample_traffic, signature, variants)
from .runner import run_scenario


def amount(sc):
    p = sc.steps[0].proposal
    if isinstance(p, PurchaseProposal):
        q = sc.steps[0].quotes[p.quote_id]
        return landed_cents(q.unit_cents, p.qty, q.tax_bp, q.shipping_cents, q.fees_cents)
    return p.amount_cents


def gate_leaks(n=100):
    cov = [f for f in M3_FAMILIES if FAMILIES[f][1] == "adversarial"]
    unc = [f for f in UNTESTED_M2 if FAMILIES[f][1] == "adversarial"] + list(COMPOUND)
    out = {}
    for name, fams in (("covered", cov), ("uncovered", unc)):
        k = tot = 0
        for f in fams:
            for v in range(n):
                tot += 1
                k += run_scenario(build_any(f, v), "gate")["kind"] == "unsafe_execution"
        out[name] = (k, tot)
    return out


def amounts(step=5):
    haz = [f for f in list(M3_FAMILIES) + list(UNTESTED_M2) + list(COMPOUND)
           if f in COMPOUND or FAMILIES[f][1] == "adversarial"]
    ben = [f for f in FAMILIES if FAMILIES[f][1] == "benign"]
    return (tuple(amount(build_any(f, v)) for f in haz for v in range(0, 100, step)),
            tuple(amount(build_any(f, v)) for f in ben for v in range(0, 100, step)))


def evidence_counts(results=None, arm="annotated+primed"):
    if not results:
        return {"seen": (5, 150), "withheld": (50, 60), "ben_fa": (24, 120)}
    rows = [r for r in json.load(open(results))["rows"] if r["model"] == arm and r["executor"] == "gate"]
    adv = [r for r in rows if r["kind_ep"] == "adversarial"]
    seen = [r for r in adv if not r["withheld"]]
    wh = [r for r in adv if r["withheld"]]
    ben = [r for r in rows if r["kind_ep"] == "benign"]
    return {"seen": (sum(r["attempt_bad"] for r in seen), len(seen)),
            "withheld": (sum(r["attempt_bad"] for r in wh), len(wh)),
            "ben_fa": (sum(r["kind"] == "false_alarm" for r in ben), len(ben))}


# ---- part 1: estimator accuracy ----------------------------------------------------------------------
def estimator_table(cal, ws=(0.0, 0.05, 0.1, 0.2, 0.4), size=1500, reps=40):
    rng, out = random.Random(11), []
    for w in ws:
        hats, cover = [], 0
        for r in range(reps):
            traffic = sample_traffic(w, size, rng)
            e = estimate_uncovered_share(traffic, cal, n_boot=150, seed=r)
            hats.append(e["w_hat"])
            cover += e["lo"] <= w <= e["hi"]
        out.append((w, st.mean(hats), min(hats), max(hats), cover / reps))
    return out


# ---- part 2: drift test -------------------------------------------------------------------------------
def drift_test(ev, biz, cal, gl, months=12, seeds=30, n_req=500, n_audit=400):
    a_c_pt, a_u_pt = ev["seen"][0] / ev["seen"][1], ev["withheld"][0] / ev["withheld"][1]
    a_c_ub, a_u_ub = cp_upper(*ev["seen"]), cp_upper(*ev["withheld"])
    g_c_ub, g_u_ub = cp_upper(*gl["covered"]), cp_upper(*gl["uncovered"])
    fa_pt = ev["ben_fa"][0] / ev["ben_fa"][1]
    g_true = 0.001
    w_true = [0.05 + (0.45 - 0.05) * t / (months - 1) for t in range(months)]
    rho_true = [0.002 + (0.02 - 0.002) * t / (months - 1) for t in range(months)]
    res = {p: {"viol": [], "rev": [], "ceil": [], "revoked": []} for p in ("fixed", "static", "online")}
    trace = None
    for s in range(seeds):
        rng = random.Random(100 + s)
        hist, ceilings, c_static = [], {}, None
        trace_s = []
        for t in range(months):
            traffic = sample_traffic(w_true[t], n_req, rng)
            est = estimate_uncovered_share(traffic, cal, n_boot=120, seed=s * 100 + t)
            k = sum(rng.random() < rho_true[t] for _ in range(n_audit))
            hist = (hist + [(k, n_audit)])[-3:]
            rho_ub = cp_upper(sum(h[0] for h in hist), sum(h[1] for h in hist))
            e_ub = Evidence(a_c_ub, a_u_ub, g_c_ub, g_u_ub, est["hi"], rho_ub, fa_pt)
            pick = choose_ceiling(e_ub, biz)
            if c_static is None:
                c_static = pick["ceiling"]
            truth = Evidence(a_c_pt, a_u_pt, g_true, g_true, w_true[t], rho_true[t], fa_pt)
            for name, c in (("fixed", BASE_CEILING), ("static", c_static), ("online", pick["ceiling"])):
                res[name]["viol"].append(expected_loss(c, truth, biz) > biz.budget)
                res[name]["rev"].append(review_cost(c, truth, biz))
                res[name]["ceil"].append(c)
                res[name]["revoked"].append(c == 0)
            trace_s.append((t, w_true[t], est["w_hat"], rho_true[t], rho_ub, pick["ceiling"],
                            expected_loss(BASE_CEILING, truth, biz), expected_loss(pick["ceiling"], truth, biz)))
        if trace is None:
            trace = trace_s
    return res, trace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=None)
    ap.add_argument("--arm", default="annotated+primed")
    ap.add_argument("--budgets", default="100,60,40", help="annual loss budgets in dollars, comma separated")
    ap.add_argument("--out", default="exp5/data/results_w2.json")
    a = ap.parse_args()
    cal = calibrate()
    print(f"calibration: beta={cal['beta']:.3f} ({cal['beta_k']}/{cal['beta_n']})  "
          f"tau={cal['tau']:.3f} ({cal['tau_k']}/{cal['tau_n']}; leave-one-family-out)")
    true_unc = UNTESTED_M2 + tuple(COMPOUND)
    det = sum(signature(s) not in cal["reference"] for f in true_unc for s in variants(f, 100)) / (100 * len(true_unc))
    print(f"true detection rate on the {len(true_unc)} uncovered families: {det:.3f} (estimated tau {cal['tau']:.3f})")
    print("\nPART 1  uncovered-share estimator, 1500 requests per run, 40 runs per row")
    print(f"{'true w':>8}{'mean w_hat':>12}{'min':>8}{'max':>8}{'95% CI holds the truth':>26}")
    rows1 = estimator_table(cal)
    for w, m, lo, hi, cv in rows1:
        print(f"{w:>8.2f}{m:>12.3f}{lo:>8.3f}{hi:>8.3f}{cv:>26.0%}")
    gl = gate_leaks()
    haz, ben = amounts()
    ev = evidence_counts(a.results, a.arm)
    budgets = [float(x) for x in a.budgets.split(",")]
    print(f"\nevidence ({a.arm}): proposer bad on seen {ev['seen']}, on withheld {ev['withheld']}; "
          f"benign refusals {ev['ben_fa']}; gate leaks covered {gl['covered']}, uncovered {gl['uncovered']}")
    base = Business(haz=haz, ben=ben)
    print(f"business: {base.lam:.0f} requests/yr, {base.f:.0%} hazardous, ${base.review_cost}/review, "
          f"owner-miss {base.human_miss:.0%} (assumed), base ceiling $500")
    print("\nPART 2  12-month drift: uncovered share 5% to 45%, unmodeled hazard share 0.2% to 2.0%.")
    print("30 runs per budget. 'Months over budget' counts months whose TRUE expected annual loss exceeds the budget.")
    summary, trace = {}, None
    for bud in budgets:
        biz = Business(haz=haz, ben=ben, budget=bud)
        res, tr = drift_test(ev, biz, cal, gl)
        if bud == budgets[len(budgets) // 2]:
            trace = (bud, tr)
        print(f"\nbudget ${bud:.0f}/yr")
        print(f"{'policy':<10}{'months over budget':>20}{'mean review $/yr':>18}{'months revoked':>16}{'mean ceiling $':>16}")
        for p, r in res.items():
            v = sum(r["viol"]) / len(r["viol"]) * 12
            summary[(bud, p)] = {"months_over": v, "review": st.mean(r["rev"]),
                                 "revoked": sum(r["revoked"]) / len(r["revoked"]) * 12, "ceiling": st.mean(r["ceil"]) / 100}
            print(f"{p:<10}{v:>20.1f}{summary[(bud, p)]['review']:>18.0f}{summary[(bud, p)]['revoked']:>16.1f}{summary[(bud, p)]['ceiling']:>16.0f}")
    print("\nPART 3  evidence scarcity at budget $60/yr: monthly probes available to estimate the unmodeled hazard share")
    print("(probes are red-team or mutation-matrix runs; real incidents are far too rare, about 3 hazardous requests a month here)")
    print(f"{'probes/month':>13}{'months over budget':>20}{'mean review $/yr':>18}{'mean ceiling $':>16}{'months revoked':>16}")
    sens = {}
    for na in (400, 100, 25):
        res, _ = drift_test(ev, Business(haz=haz, ben=ben, budget=60.0), cal, gl, n_audit=na)
        r = res["online"]
        sens[na] = {"months_over": sum(r["viol"]) / len(r["viol"]) * 12, "review": st.mean(r["rev"]),
                    "ceiling": st.mean(r["ceil"]) / 100, "revoked": sum(r["revoked"]) / len(r["revoked"]) * 12}
        print(f"{na:>13}{sens[na]['months_over']:>20.1f}{sens[na]['review']:>18.0f}{sens[na]['ceiling']:>16.0f}{sens[na]['revoked']:>16.1f}")
    bud, tr = trace
    print(f"\none run at budget ${bud:.0f}/yr")
    print(f"{'month':>6}{'true w':>8}{'w_hat':>7}{'true rho':>9}{'rho UB':>8}{'ceiling $':>10}{'loss at $500':>14}{'loss at pick':>14}")
    for t, w, wh, r, ru, c, l5, lp in tr:
        print(f"{t + 1:>6}{w:>8.2f}{wh:>7.2f}{r:>9.3f}{ru:>8.3f}{c / 100:>10.0f}{l5:>14.0f}{lp:>14.0f}")
    json.dump({"calibration": {k: v for k, v in cal.items() if k != "reference"}, "estimator": rows1,
               "evidence": ev, "gate_leaks": gl, "summary": {f"{k[0]}|{k[1]}": v for k, v in summary.items()}, "scarcity": sens, "trace": trace}, open(a.out, "w"), default=str)


if __name__ == "__main__":
    main()
