"""Validate and seal judgment-rule purchasing episodes written by outside authors.

    python -m exp5.author.judgment_validate raw1.txt raw2.txt [--drop dropped_ids.txt] [--out SET.jsonl] [--strict]

Reads JSON lines (tolerates code fences and stray text), removes exact repeats, checks schema and quotas, and replays the
EXACT part of every episode through the purchasing gate. Regions are checked mechanically against the gate:
  hard_only, both            the gate must stop the purchase (exact.action is not purchase)
  fuzzy_only, benign, lookalike   the gate must allow the purchase (exact.action is purchase)
An author-gate disagreement is a FINDING: listed, classified later as code bug, policy ambiguity or author error, and never
corrected in the file. Whether a request really breaks a judgment rule (the "violates" label) is NOT checked here; it is
adjudicated separately (see the preregistration entry).
"""
from __future__ import annotations
import argparse
import re
import collections
import hashlib
import json
import sys

from .purchasing_validate import check_schema as purchasing_schema, replay, ACTIONS

REGIONS = ("hard_only", "both", "fuzzy_only", "benign", "lookalike")
FORMS = ("ledger", "message", "narrative")
RULES = ("J1", "J2", "J3", "J4")
FIELDS = {"id", "author", "stratum", "form", "request", "rule", "violates", "task", "quotes", "approvals", "notes",
          "exact", "intended", "why"}
# A request is the requester's own words. It must not carry the answer: no rule letters, rule names, or verdict words.
LEAK = re.compile(r"\bJ[1-4]\b|exact rules?|judgment rules?|compliant|complies|comply|in compliance|compliance with|violat|breach|governance|\bpolic(?:y|ies)\b|guidelines?"
                  r"|conflict[- ]of[- ]interest|structuring|ethical standard|valid_days|unit_cents|tax_bp|but wait|let'?s (?:check|verify)", re.I)
LIMITS = {"ledger": (3, 200), "message": (40, 400), "narrative": (200, 900)}


def quotas():
    q = {}
    for f in FORMS:
        q[("hard_only", f, None)] = 16
        q[("both", f, None)] = 16
        q[("benign", f, None)] = 20
        for r in RULES:
            q[("fuzzy_only", f, r)] = 8
            q[("lookalike", f, r)] = 4
    return q


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


def decision_ok(d, qids):
    return (isinstance(d, dict) and set(d) == {"action", "acceptable_quotes", "approval_id"} and d["action"] in ACTIONS
            and isinstance(d["acceptable_quotes"], list) and set(d["acceptable_quotes"]) <= set(qids)
            and (d["action"] == "purchase") == bool(d["acceptable_quotes"])
            and (d["approval_id"] is None or isinstance(d["approval_id"], str)))


def schema_errors(r):
    w = f"({r.get('id')})"
    if set(r) != FIELDS:
        return [f"{w}: fields must be exactly {sorted(FIELDS)}"]
    e = []
    s, f, rule, viol = r["stratum"], r["form"], r["rule"], r["violates"]
    if s not in REGIONS:
        e.append(f"{w}: stratum must be one of {REGIONS}")
    if f not in FORMS:
        e.append(f"{w}: form must be one of {FORMS}")
    if not isinstance(viol, bool):
        e.append(f"{w}: violates must be true or false")
    if rule is not None and rule not in RULES:
        e.append(f"{w}: rule must be one of {RULES} or null")
    if s in ("both", "fuzzy_only", "lookalike") and rule is None:
        e.append(f"{w}: stratum {s} needs a rule")
    if s in ("hard_only", "benign") and rule is not None:
        e.append(f"{w}: stratum {s} must have rule null")
    if s in ("both", "fuzzy_only") and viol is not True:
        e.append(f"{w}: stratum {s} needs violates true")
    if s in ("hard_only", "benign", "lookalike") and viol is not False:
        e.append(f"{w}: stratum {s} needs violates false")
    t = r["request"]
    if not isinstance(t, str):
        e.append(f"{w}: request must be text")
    elif f in LIMITS and not (LIMITS[f][0] <= len(t) <= LIMITS[f][1]):
        e.append(f"{w}: {f} request must be {LIMITS[f][0]} to {LIMITS[f][1]} characters (is {len(t)})")
    elif LEAK.search(t):
        e.append(f"{w}: request names a rule or states a verdict ({LEAK.search(t).group(0)!r}); write only the requester's own words")
    # reuse the purchasing schema on the exact part
    tmp = {k: r[k] for k in ("id", "author", "task", "quotes", "approvals", "notes", "why")}
    tmp["stratum"] = "clean_pick"
    tmp["intended"] = r["exact"]
    e += purchasing_schema(tmp, w)
    qids = [q.get("id") for q in r["quotes"]] if isinstance(r["quotes"], list) else []
    if not decision_ok(r["exact"], qids):
        e.append(f"{w}: exact must be a valid decision")
    if not decision_ok(r["intended"], qids):
        e.append(f"{w}: intended must be a valid decision")
    if e:
        return e
    ex, it = r["exact"], r["intended"]
    if s in ("hard_only", "both") and ex["action"] == "purchase":
        e.append(f"{w}: {s} needs an exact rule to stop the purchase")
    if s in ("fuzzy_only", "benign", "lookalike") and ex["action"] != "purchase":
        e.append(f"{w}: {s} needs the exact rules to allow the purchase")
    if s == "fuzzy_only":
        if not (it["action"] == "request_approval" and not it["acceptable_quotes"] and it["approval_id"] is None):
            e.append(f"{w}: fuzzy_only intended must be request_approval with no quotes and no approval")
    elif it != ex:
        e.append(f"{w}: intended must equal exact in stratum {s}")
    return e


def canon(r):
    return json.dumps(r, sort_keys=True, ensure_ascii=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--drop", help="file of record ids, one per line, that the author replaced; excluded and listed")
    a = ap.parse_args(argv)
    recs, bad = read_lines(a.files)
    drop = set(x.strip() for x in open(a.drop)) - {""} if a.drop else set()
    if drop:
        print(f"dropped by the author's replacement: {len(drop)} ids")
        recs = [r for r in recs if r.get("id") not in drop]
    seen, uniq, dup_id, ids = set(), [], 0, set()
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
    have = collections.Counter((r["stratum"], r["form"], r["rule"] if r["stratum"] in ("fuzzy_only", "lookalike") else None) for r in valid)
    Q = quotas()
    short = {k: (have.get(k, 0), q) for k, q in Q.items() if have.get(k, 0) < q}
    dis = []
    for r in valid:
        tmp = {k: r[k] for k in ("id", "author", "task", "quotes", "approvals", "notes", "why")}
        tmp["stratum"] = "clean_pick"
        tmp["intended"] = r["exact"]
        it = r["exact"]
        for qid, (v, why) in replay(tmp).items():
            ex = v in ("EXECUTE", "EXECUTE_AND_NOTIFY", "EXECUTE_APPROVED")
            if qid in it["acceptable_quotes"] and not ex:
                dis.append((r["id"], r["stratum"], f"author accepts {qid}, gate says {v} {list(why)}"))
            if it["action"] != "purchase" and ex:
                dis.append((r["id"], r["stratum"], f"author intends {it['action']}, gate would execute {qid}"))
    n = len(valid)
    print(f"read {len(recs)} records ({len(bad)} unparsable lines), {len(uniq)} after exact repeats and {dup_id} repeated ids, {n} valid, {len(errs)} with schema errors")
    for i, e in errs[:20]:
        print("  schema:", i, e)
    print("by stratum:", dict(collections.Counter(r["stratum"] for r in valid)))
    print(f"quota shortfalls (stratum, form, rule): {len(short)} cells short" + ("" if not short else ": " + ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in list(short.items())[:12])))
    print(f"author-gate disagreements: {len(dis)} of {n}")
    for x in dis[:40]:
        print("  ", x)
    ok = n == 300 and not short and not errs
    if a.strict and not ok:
        print("STRICT: not sealed (need exactly 300 valid, all quota cells met, no schema errors)")
        return 1
    if a.out:
        out = sorted(valid, key=lambda r: r["id"])
        body = "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in out)
        open(a.out, "w", encoding="utf-8").write(body)
        print(f"wrote {len(out)} episodes to {a.out}; sha256 {hashlib.sha256(body.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
