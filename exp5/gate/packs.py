"""Spec packs: declarative YAML, no code. Packs may only TIGHTEN the owner's base limits.

A pack that tries to loosen any limit, or to set anything outside the allowed
rule keys (budget, accounts, vendors, list items), is rejected whole.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass, replace

import yaml

from .models import Limits

# key -> "min" (smaller is stricter) | "max" (larger is stricter)
TIGHTEN_DIRECTION = {
    "a_max_cents": "min",
    "b_max_cents": "min",
    "window_cap_cents": "min",
    "delta_bp": "min",
    "hug_margin_bp": "max",
    "hug_inclusive": "any",
    "gamma_bp": "max",
    "delivery_margin_days": "max",
}
SET_KEYS = {"c_categories"}          # union only; a pack can add, never remove
ALLOWED_RULES = set(TIGHTEN_DIRECTION) | SET_KEYS
ALLOWED_TOP = {"name", "version", "domain", "changelog", "rules", "tests"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class PackError(ValueError):
    pass


@dataclass(frozen=True)
class Pack:
    name: str
    version: str
    domain: str
    rules: dict
    tests: tuple
    sha256: str


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def parse_pack(text: str) -> Pack:
    doc = yaml.safe_load(text)           # safe_load only: packs cannot carry code
    if not isinstance(doc, dict):
        raise PackError("pack must be a YAML mapping")
    extra = set(doc) - ALLOWED_TOP
    if extra:
        raise PackError(f"unknown top-level keys: {sorted(extra)}")
    for k in ("name", "version", "domain", "changelog", "rules"):
        if k not in doc:
            raise PackError(f"missing required key: {k}")
    if not isinstance(doc["name"], str) or not doc["name"].strip():
        raise PackError("name must be a non-empty string")
    if not isinstance(doc["version"], str) or not VERSION_RE.match(doc["version"]):
        raise PackError("version must look like 1.2.3")
    if not isinstance(doc["changelog"], str) or not doc["changelog"].strip():
        raise PackError("changelog is required")
    rules = doc["rules"]
    if not isinstance(rules, dict) or not rules:
        raise PackError("rules must be a non-empty mapping")
    bad = set(rules) - ALLOWED_RULES
    if bad:
        raise PackError(f"rule keys not allowed in packs: {sorted(bad)}")
    clean = {}
    for k, v in rules.items():
        if k in TIGHTEN_DIRECTION:
            if not _is_int(v):
                raise PackError(f"{k} must be a non-negative integer")
            clean[k] = v
        else:
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise PackError(f"{k} must be a list of strings")
            clean[k] = tuple(sorted(set(v)))
    tests = doc.get("tests", [])
    if not isinstance(tests, list):
        raise PackError("tests must be a list")
    canon = json.dumps(doc, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canon.encode()).hexdigest()
    return Pack(doc["name"], doc["version"], str(doc["domain"]), clean,
                tuple(tests), digest)


def _looser(direction: str, pack_v: int, base_v: int) -> bool:
    return pack_v > base_v if direction == "min" else pack_v < base_v


def check_tightens(base: Limits, pack: Pack) -> None:
    for k, direction in TIGHTEN_DIRECTION.items():
        if k in pack.rules and _looser(direction, pack.rules[k], getattr(base, k)):
            raise PackError(
                f"pack {pack.name}@{pack.version} loosens {k}: "
                f"{pack.rules[k]} vs base {getattr(base, k)}")


def compose(base: Limits, packs: list) -> Limits:
    """Most restrictive wins on every dimension. Only the base can grant authority."""
    names = [p.name for p in packs]
    if len(set(names)) != len(names):
        raise PackError("duplicate pack names")
    vals = {k: getattr(base, k) for k in TIGHTEN_DIRECTION}
    cats = set(base.c_categories)
    for p in packs:
        check_tightens(base, p)
        for k, direction in TIGHTEN_DIRECTION.items():
            if k in p.rules:
                vals[k] = min(vals[k], p.rules[k]) if direction == "min" \
                    else max(vals[k], p.rules[k])
        cats |= set(p.rules.get("c_categories", ()))
    vals["a_max_cents"] = min(vals["a_max_cents"], vals["b_max_cents"])
    return replace(base, c_categories=frozenset(cats), **vals)
