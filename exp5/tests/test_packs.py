import pytest

from exp5.gate import Limits, PackError, compose, parse_pack

BASE = Limits()


def pack(rules_yaml, name="p1", extra=""):
    return parse_pack(f"""
name: {name}
version: 1.0.0
domain: test
changelog: initial
rules:
{rules_yaml}
{extra}
""")


def test_tightening_pack_applies():
    p = pack("  b_max_cents: 20000\n  gamma_bp: 2500\n  c_categories: [perishables]")
    L = compose(BASE, [p])
    assert L.b_max_cents == 20000 and L.gamma_bp == 2500
    assert "perishables" in L.c_categories


def test_loosening_pack_rejected():
    with pytest.raises(PackError):
        compose(BASE, [pack("  b_max_cents: 99999")])
    with pytest.raises(PackError):
        compose(BASE, [pack("  gamma_bp: 100")])      # smaller discount demand = looser


def test_most_restrictive_wins_across_packs():
    L = compose(BASE, [pack("  b_max_cents: 30000", "p1"), pack("  b_max_cents: 10000", "p2")])
    assert L.b_max_cents == 10000


def test_a_never_exceeds_b_after_compose():
    L = compose(BASE, [pack("  b_max_cents: 3000")])
    assert L.a_max_cents == 3000


@pytest.mark.parametrize("rule", ["  budget_ceiling_cents: 5", "  vendors: [Evil]",
                                  "  accounts: [x]", "  items: [x]"])
def test_packs_cannot_touch_owner_controlled_things(rule):
    with pytest.raises(PackError):
        pack(rule)


def test_unknown_top_level_key_and_missing_changelog():
    with pytest.raises(PackError):
        pack("  b_max_cents: 100", extra="exec: rm -rf /")
    with pytest.raises(PackError):
        parse_pack("name: x\nversion: 1.0.0\ndomain: d\nrules:\n  b_max_cents: 1\n")


def test_yaml_code_tags_are_rejected():
    with pytest.raises(Exception):
        parse_pack("name: !!python/object/apply:os.system ['echo hi']\n")


def test_hash_is_stable_and_content_sensitive():
    a = pack("  b_max_cents: 100")
    assert a.sha256 == pack("  b_max_cents: 100").sha256
    assert a.sha256 != pack("  b_max_cents: 101").sha256


def test_duplicate_pack_names_rejected():
    with pytest.raises(PackError):
        compose(BASE, [pack("  b_max_cents: 100"), pack("  b_max_cents: 200")])
