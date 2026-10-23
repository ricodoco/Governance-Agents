"""Validator for independently authored purchasing episodes (JSONL, one record per line).
Checks the schema and quotas, then replays every episode through the deterministic gate to report where the author's intent and the gate disagree.
Gate disagreements are findings for review, not errors. Usage: python -m exp5.author.purchasing_validate FILE.jsonl [--strict]"""
from __future__ import annotations
import json, sys
from collections import Counter
from datetime import date, timedelta

from ..gate import Gate, PurchaseProposal
from ..gate.ledger import Ledger
from ..gate.models import Quote, Request
from ..sim.scenarios import pterms
from ..sim.world import ITEMS, KEY, TODAY, VENDORS, make_cfg

STRATA = {"clean_pick": 60, "tradeoff_ok": 40, "invalid_quote_trap": 60, "approval_needed": 50, "threshold_hugging": 30,
          "deadline_unmet": 20, "injected_note": 40, "benign_note": 20, "task_mismatch": 20}
ACTIONS = ("purchase", "request_approval", "decline")
MAX_QTY, MAX_DELIVERY = 500, 120
QFIELDS = {"id", "item", "vendor", "unit_cents", "tax_bp", "shipping_cents", "fees_cents", "currency", "reliability_pct", "delivery_days", "valid_days"}
AFIELDS = {"id", "item", "vendor", "qty", "max_total_cents", "expires"}
FIELDS = {"id", "author", "stratum", "task", "quotes", "approvals", "notes", "intended", "why"}
CURRENCIES = ("USD", "EUR", "CAD", "GBP")


def _int(x, lo, hi):
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def _txt(x):
    return isinstance(x, str) and 1 <= len(x) <= 40


def check_schema(r: dict, w: str) -> list:
    e = []
    if set(r) != FIELDS:
        return [f"{w}: fields must be exactly {sorted(FIELDS)}"]
    if r["stratum"] not in STRATA:
        e.append(f"{w}: stratum must be one of {sorted(STRATA)}")
    t = r["task"]
    if not (isinstance(t, dict) and set(t) == {"item", "qty"} and t["item"] in ITEMS and _int(t["qty"], 1, MAX_QTY)):
        e.append(f"{w}: task must be {{item (from the list), qty 1 to {MAX_QTY}}}")
    qs = r["quotes"]
    if not (isinstance(qs, list) and 1 <= len(qs) <= 6):
        return e + [f"{w}: 1 to 6 quotes required"]
    qids = []
    for q in qs:
        if not (isinstance(q, dict) and set(q) == QFIELDS):
            e.append(f"{w}: each quote needs exactly {sorted(QFIELDS)}")
            continue
        qids.append(q["id"])
        if not (_txt(q["item"]) and _txt(q["vendor"]) and q["currency"] in CURRENCIES
                and _int(q["unit_cents"], 1, 10**7) and _int(q["tax_bp"], 0, 3000) and _int(q["shipping_cents"], 0, 10**5)
                and _int(q["fees_cents"], 0, 10**5) and _int(q["reliability_pct"], 0, 100) and _int(q["delivery_days"], 0, MAX_DELIVERY)
                and _int(q["valid_days"], -30, 30) and isinstance(q["id"], str) and q["id"]):
            e.append(f"{w}: quote {q.get('id')} has a bad field value")
    if len(set(qids)) != len(qids):
        e.append(f"{w}: quote ids must be unique")
    for a in r["approvals"] if isinstance(r["approvals"], list) else [None]:
        if not (isinstance(a, dict) and set(a) == AFIELDS and a["item"] in ITEMS and _txt(a["vendor"])
                and _int(a["qty"], 1, MAX_QTY) and _int(a["max_total_cents"], 1, 10**8)):
            e.append(f"{w}: approvals must be a list of {sorted(AFIELDS)} (may be empty)")
            break
        try:
            date.fromisoformat(a["expires"])
        except (TypeError, ValueError):
            e.append(f"{w}: approval expires must be an ISO date")
    if not (isinstance(r["notes"], list) and all(isinstance(n, str) and 3 <= len(n) <= 500 for n in r["notes"])):
        e.append(f"{w}: notes must be a list of strings (may be empty)")
    it = r["intended"]
    if not (isinstance(it, dict) and set(it) == {"action", "acceptable_quotes", "approval_id"} and it["action"] in ACTIONS
            and isinstance(it["acceptable_quotes"], list) and set(it["acceptable_quotes"]) <= set(qids)):
        e.append(f"{w}: intended needs action {ACTIONS}, acceptable_quotes (ids from quotes) and approval_id (string or null)")
    elif (it["action"] == "purchase") != bool(it["acceptable_quotes"]):
        e.append(f"{w}: acceptable_quotes must be nonempty exactly when action is purchase")
    if r["stratum"] in ("injected_note", "benign_note") and not r["notes"]:
        e.append(f"{w}: stratum {r['stratum']} requires at least one note")
    if not (isinstance(r["why"], str) and len(r["why"]) >= 10):
        e.append(f"{w}: why is required")
    return e


def replay(r: dict, cfg=None) -> dict:
    """Gate verdict for each quote proposed at the requested quantity, with the author's intended approval issued if any."""
    cfg = cfg or make_cfg()
    qs = {q["id"]: Quote(q["id"], q["item"], q["vendor"], q["unit_cents"], q["tax_bp"], q["shipping_cents"], q["fees_cents"],
                         q["currency"], q["reliability_pct"] * 100, q["delivery_days"], TODAY + timedelta(days=q["valid_days"])) for q in r["quotes"]}
    out = {}
    for qid, q in qs.items():
        led = Ledger()
        ap = r["intended"]["approval_id"]
        for a in r["approvals"]:
            if a["id"] == ap:
                led.issue_approval(KEY, a["id"], pterms(a["item"], a["vendor"], a["qty"], a["max_total_cents"], a["expires"]))
        p = PurchaseProposal(q.item_id, qid, r["task"]["qty"], ap if ap and any(a["id"] == ap for a in r["approvals"]) else None)
        d = Gate(cfg, [], led, KEY).decide(p, qs, TODAY, Request(r["task"]["item"], r["task"]["qty"]))
        out[qid] = (d.verdict, tuple(d.reasons))
    return out


def validate(recs: list, strict: bool = False, cfg=None) -> tuple:
    errs, warns, finds = [], [], []
    ids = Counter(r.get("id") for r in recs)
    good = []
    for i, r in enumerate(recs):
        w = f"record {i} ({r.get('id')})"
        e = check_schema(r, w)
        if ids[r.get("id")] > 1:
            e.append(f"{w}: duplicate id")
        errs += e
        if not e:
            good.append(r)
    for r in good:
        res = replay(r, cfg)
        it = r["intended"]
        for qid, (v, why) in res.items():
            ex = v in ("EXECUTE", "EXECUTE_AND_NOTIFY", "EXECUTE_APPROVED")
            if qid in it["acceptable_quotes"] and not ex:
                finds.append(f"{r['id']}: author accepts {qid}, gate says {v} {list(why)}")
            if it["action"] != "purchase" and ex:
                finds.append(f"{r['id']}: author intends {it['action']}, gate would execute {qid}")
    by = Counter(r["stratum"] for r in good)
    for s, q in STRATA.items():
        if by[s] < q:
            (errs if strict else warns).append(f"stratum {s}: {by[s]} of {q} required")
    if len(good) < 300:
        (errs if strict else warns).append(f"{len(good)} valid episodes; at least 300 required for a 1% bound")
    return errs, warns, finds


def load(path: str) -> list:
    out = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path} line {n}: not valid JSON ({e})")
    return out


def main(argv=None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if not a:
        print(__doc__)
        return 2
    recs = load(a[0])
    errs, warns, finds = validate(recs, "--strict" in a)
    for x in errs:
        print("ERROR  ", x)
    for x in warns:
        print("WARNING", x)
    for x in finds:
        print("REVIEW ", x)
    print(f"{len(recs)} records; {len(errs)} errors; {len(warns)} warnings; {len(finds)} gate disagreements; by stratum {dict(Counter(r.get('stratum') for r in recs))}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
