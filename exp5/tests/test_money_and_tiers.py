import pytest
from hypothesis import given, settings, strategies as st

from exp5.gate import Limits, amount_level, landed_cents, tier


def test_landed_components():
    assert landed_cents(1000, 3, 825, 499, 150) == 3000 + 248 + 499 + 150  # 247.5 -> 248


def test_landed_rounds_half_up():
    assert landed_cents(100, 1, 5000, 0, 0) == 150
    assert landed_cents(1, 1, 5000, 0, 0) == 2        # 0.5 -> 1


@pytest.mark.parametrize("bad", [(-1, 1, 0, 0, 0), (1, 0, 0, 0, 0), (1.5, 1, 0, 0, 0),
                                 (True, 1, 0, 0, 0), (1, 1, -1, 0, 0)])
def test_landed_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        landed_cents(*bad)


def test_tier_boundaries_inclusive():
    L = Limits(hug_margin_bp=0)
    assert tier(5000, L) == "A" and tier(5001, L) == "B"
    assert tier(50000, L) == "B" and tier(50001, L) == "C"


def test_threshold_hugging_bumps_one_level():
    L = Limits(hug_margin_bp=200)
    assert amount_level(4999, L) == "B"      # $49.99 hugs the $50 line
    assert amount_level(4900, L) == "A"      # exactly 2% below: not hugging
    assert amount_level(4901, L) == "B"
    assert amount_level(49999, L) == "C"     # $499.99 hugs the $500 line
    assert amount_level(50000, L) == "C"


@settings(max_examples=300, deadline=None)
@given(a=st.integers(0, 200_000), b=st.integers(0, 200_000),
       hug=st.sampled_from([0, 50, 200, 500]))
def test_amount_level_is_monotone(a, b, hug):
    L = Limits(hug_margin_bp=hug)
    lo, hi = sorted((a, b))
    order = {"A": 0, "B": 1, "C": 2}
    assert order[amount_level(lo, L)] <= order[amount_level(hi, L)]


# ---------------------------------------------------------------- the policy-independence finding (DEVIATIONS 34, 36, 37)
# An outside model wrote an owner brief; a program mapped its seven parameters to the guard. The brief and
# the guard then disagreed at exactly one place: a total sitting exactly ON the cushion's edge. The brief put
# it at the higher level, the guard at the lower one, and two of 340 episodes ended in unsafe executions at
# exactly $475.00. limits.hug_inclusive is the repair. It is off by default, so every result in the paper
# that predates it still holds; an owner who reads the cushion as the brief did turns it on.
BRIEF_LIMITS = dict(a_max_cents=10_000, b_max_cents=50_000, hug_margin_bp=500)


def test_a_total_on_the_cushion_edge_tiers_low_by_default():
    lim = Limits(**BRIEF_LIMITS)
    assert amount_level(9_500, lim) == "A"      # the brief says B
    assert amount_level(47_500, lim) == "B"     # the brief says C, and this is where the two breaches were


def test_hug_inclusive_moves_the_cushion_edge_to_the_higher_level():
    lim = Limits(**BRIEF_LIMITS, hug_inclusive=True)
    assert amount_level(9_500, lim) == "B"
    assert amount_level(47_500, lim) == "C"


def test_hug_inclusive_changes_nothing_except_the_edge():
    off, on = Limits(**BRIEF_LIMITS), Limits(**BRIEF_LIMITS, hug_inclusive=True)
    differ = [a for a in range(0, 60_001) if amount_level(a, off) != amount_level(a, on)]
    assert differ == [9_500, 47_500]
