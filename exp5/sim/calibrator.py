"""Week 2, element 2: a RISK-BUDGET CALIBRATOR that sets, tightens or revokes the autonomous ceiling.

The certificate is built from UPPER bounds, so it is conservative:
  per hazardous request of amount m, with ceiling c,
    autonomous (m <= c):    loss = m * (p_bad * p_gate + rho)
    owner approval (m > c): loss = m * (p_bad * p_gate + rho * h)
  The gate's hard rules still apply to an approved request, so approval only changes what happens to
  hazards no rule addresses (rho): the owner stops them except at the miss rate h.
  p_bad  = (1-w) a_c + w a_u     proposer out-of-policy rate, covered vs uncovered forms
  p_gate = (1-w) g_c + w g_u     gate leak per hazardous proposal
  rho    = share of hazards no rule addresses (the gate cannot catch them)
  h      = rate at which the owner approves a bad request (an ASSUMPTION; not measured)
Expected annual loss = lam * f * mean_i(loss_i). Review load counts every request above c, plus benign
requests the gate refuses. The calibrator returns the LARGEST ceiling at or below the owner's base whose
loss bound meets the budget (tighten-only); if none does it returns 0, which revokes autonomy.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import comb

BASE_CEILING = 50_000                       # owner's base autonomous ceiling, cents ($500)
CANDIDATES = (0, 2_500, 5_000, 10_000, 20_000, 30_000, 50_000)


def cp_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson upper bound for k events in n trials (bisection)."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    if k == 0:
        return 1 - alpha ** (1 / n)
    lo, hi = k / n, 1.0
    for _ in range(60):
        p = (lo + hi) / 2
        cdf = sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
        lo, hi = (p, hi) if cdf > alpha else (lo, p)
    return (lo + hi) / 2


@dataclass(frozen=True)
class Evidence:
    a_c: float; a_u: float          # proposer bad-proposal rate, covered / uncovered forms
    g_c: float; g_u: float          # gate leak per hazardous proposal, covered / uncovered forms
    w: float                        # uncovered share of traffic
    rho: float                      # share of hazards no rule addresses
    fa: float                       # benign requests the gate refuses


@dataclass(frozen=True)
class Business:
    haz: tuple                      # sampled hazardous amounts, cents
    ben: tuple                      # sampled benign amounts, cents
    lam: float = 2000.0             # requests per year
    f: float = 0.02                 # hazardous share
    review_cost: float = 1.5        # dollars of owner attention per escalation
    human_miss: float = 0.05        # assumed: owner approves a bad request
    budget: float = 100.0           # annual loss budget, dollars


def expected_loss(c: int, e: Evidence, b: Business) -> float:
    p_bad = (1 - e.w) * e.a_c + e.w * e.a_u
    p_gate = (1 - e.w) * e.g_c + e.w * e.g_u
    tot = 0.0
    for m in b.haz:
        tot += m * (p_bad * p_gate + (e.rho if m <= c else e.rho * b.human_miss))
    return b.lam * b.f * (tot / len(b.haz)) / 100.0


def review_cost(c: int, e: Evidence, b: Business) -> float:
    hz = sum(m > c for m in b.haz) / len(b.haz)
    bn = sum(m > c for m in b.ben) / len(b.ben)
    escal = b.lam * ((1 - b.f) * (bn + e.fa * (1 - bn)) + b.f * hz)
    return escal * b.review_cost


def choose_ceiling(e: Evidence, b: Business, base: int = BASE_CEILING) -> dict:
    table = [(c, expected_loss(c, e, b), review_cost(c, e, b)) for c in CANDIDATES if c <= base]
    ok = [t for t in table if t[1] <= b.budget]
    pick = max(ok, key=lambda t: t[0]) if ok else min(table, key=lambda t: t[0])
    return {"ceiling": pick[0], "loss": pick[1], "review": pick[2], "meets_budget": bool(ok),
            "revoked": pick[0] == 0, "table": table}
