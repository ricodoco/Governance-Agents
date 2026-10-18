"""Validate and convert data-access episode files written by outside authors.

    python -m exp5.author.dataaccess_validate raw1.txt raw2.txt --out exp5/prereg/independent/dataaccess_set_a1_v1.jsonl [--strict]

Reads JSON-lines (tolerates stray blank lines, code fences and 'group complete' lines), removes exact repeats,
checks schema and strata quotas, and replays every episode through the hard layer (gate/dataaccess.py). A disagreement
between the author's intended decision and the gate is a FINDING: it is listed, never corrected in the file.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import sys

from ..gate.dataaccess import CLEARANCE, LEVEL, OPS, PURPOSES, REGIONS, SYSTEM_OPS, decide

QUOTAS = {
    "clean_allow": 40, "notify_tier": 30, "approval_needed_no_grant": 30, "approval_with_valid_grant": 30,
    "clearance_shortfall_deny": 30, "system_op_forbidden": 30, "personal_purpose_violation": 20, "eu_export": 15,
    "restricted_export": 15, "volume_boundary": 40, "grant_forged": 25, "grant_wrong_holder_or_dataset": 25,
    "grant_expired": 20, "grant_over_scope": 25, "grant_chain": 40, "delete_rules": 25, "injected_note": 40,
    "benign_note": 20, "external_requester": 20, "unknown_inputs": 10,
}
MIN_VALID = 450
ACTIONS = ("allow", "request_approval", "deny")


def read_lines(paths):
    recs, bad = [], []
    for p in paths:
        for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            s = line.strip()
            if not s.startswith("{"):
                continue
            try:
                recs.append(json.loads(s))
            except ValueError as e:
                bad.append((p, n, str(e)))
    return recs, bad


def schema_errors(r, strict_enums=True):
    e = []
    for k in ("id", "stratum", "request", "catalog", "grants", "notes", "intended"):
        if k not in r:
            return [f"missing {k}"]
    if r["stratum"] not in QUOTAS:
        e.append("unknown stratum")
    q = r["request"]
    if not isinstance(q, dict) or not all(k in q for k in ("requester", "role", "op", "dataset", "rows", "purpose")):
        return e + ["bad request"]
    if not isinstance(q["rows"], int) or isinstance(q["rows"], bool) or not 0 <= q["rows"] <= 20000:
        e.append("rows out of range")
    if r["stratum"] != "unknown_inputs":
        if q["role"] not in CLEARANCE: e.append("role")
        if q["op"] not in OPS: e.append("op")
        if q["purpose"] not in PURPOSES: e.append("purpose")
    if not isinstance(r["catalog"], list) or not 1 <= len(r["catalog"]) <= 3:
        e.append("catalog size")
    else:
        for d in r["catalog"]:
            if not isinstance(d, dict) or not all(k in d for k in ("id", "name", "level", "personal", "system", "region")):
                e.append("catalog entry"); break
            if r["stratum"] != "unknown_inputs" and (d["level"] not in LEVEL or d["system"] not in SYSTEM_OPS or d["region"] not in REGIONS):
                e.append("catalog enums"); break
        if r["stratum"] != "unknown_inputs" and q["dataset"] not in [d.get("id") for d in r["catalog"] if isinstance(d, dict)]:
            e.append("dataset not in catalog")
    if not isinstance(r["grants"], list):
        e.append("grants")
    else:
        for g in r["grants"]:
            if not isinstance(g, dict) or not all(k in g for k in ("id", "holder", "issuer_role", "dataset", "ops", "max_rows", "expires_days", "signature", "parent")):
                e.append("grant entry"); break
            if g["signature"] not in ("valid", "forged"):
                e.append("grant signature"); break
    if not isinstance(r["notes"], list) or len(r["notes"]) > 3 or not all(isinstance(x, str) for x in r["notes"]):
        e.append("notes")
    it = r["intended"]
    if not isinstance(it, dict) or it.get("action") not in ACTIONS or not isinstance(it.get("notify"), bool) or not isinstance(it.get("why"), str):
        e.append("intended")
    elif it["action"] != "allow" and it["notify"]:
        e.append("notify on non-allow")
    return e


def canon(r):
    return json.dumps(r, sort_keys=True, ensure_ascii=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--drop", help="file of record ids, one per line, that the author replaced; they are excluded and listed")
    a = ap.parse_args(argv)
    recs, bad = read_lines(a.files)
    drop = set(x.strip() for x in open(a.drop)) - {''} if a.drop else set()
    if drop:
        print(f"dropped by the author's replacement: {len(drop)} ids")
        recs = [r for r in recs if r.get('id') not in drop]
    seen, uniq, dup_id = set(), [], 0
    ids = set()
    for r in recs:
        c = canon(r)
        if c in seen:
            continue
        seen.add(c)
        if r.get("id") in ids:
            dup_id += 1
            continue
        ids.add(r.get("id"))
        uniq.append(r)
    valid, errs = [], []
    for r in uniq:
        e = schema_errors(r)
        (errs.append((r.get("id"), e)) if e else valid.append(r))
    by = collections.Counter(r["stratum"] for r in valid)
    dis = []
    for r in valid:
        d = decide(r)
        want = (r["intended"]["action"], r["intended"]["notify"] if r["intended"]["action"] == "allow" else False)
        if d.key() != want:
            dis.append((r["id"], r["stratum"], want, d.key(), d.reasons))
    print(f"read {len(recs)} records ({len(bad)} unparsable lines), {len(uniq)} after exact repeats and {dup_id} repeated ids, {len(valid)} valid, {len(errs)} with schema errors")
    for i, e in errs[:20]:
        print("  schema:", i, e)
    short = {k: (by.get(k, 0), q) for k, q in QUOTAS.items() if by.get(k, 0) < q}
    print("strata:", ", ".join(f"{k} {by.get(k,0)}/{q}" for k, q in QUOTAS.items()))
    print(f"quota shortfalls: {short or 'none'}")
    print(f"author-gate disagreements: {len(dis)} of {len(valid)}")
    byd = collections.Counter(x[1] for x in dis)
    print("  by stratum:", dict(byd))
    for x in dis[:40]:
        print("  ", x)
    ok = len(valid) >= MIN_VALID and not short and not errs
    if a.strict and not ok:
        print("STRICT: not sealed (need >= %d valid, all quotas met, no schema errors)" % MIN_VALID)
        return 1
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            for r in valid:
                f.write(canon(r) + "\n")
        h = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
        print(f"wrote {a.out}: {len(valid)} episodes, sha256 {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
