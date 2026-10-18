"""Blind adjudication of the 'violates' labels of the sealed judgment set (DEVIATIONS entry 44).

  python3 -m exp5.author.judgment_adjudicate make            # writes packets (judges see only request text and rule text)
  python3 -m exp5.author.judgment_adjudicate score A=fileA B=fileB R=rick.txt   # kappas and disagreements

A judge reply line is JSON {"pid":"p-001","verdict":"none"|"J1".."J4"}; Rick's file lines are 'p-001 none' or 'p-001 J3'.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, random, re, sys
from pathlib import Path

SET = Path("exp5/prereg/independent/judgment_set_a1_v1.jsonl")
OUT = Path("exp5/prereg/independent/adjudication")
PROMPT = Path("exp5/author/Author_Prompt_Judgment_v1.md")
SHUFFLE_SEED, RICK_SEED, RICK_N, BATCH = 20261018, 20261019, 60, 100
VERDICTS = ("none", "J1", "J2", "J3", "J4")


def rules_text():
    t = PROMPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(l for l in t if re.match(r"- \*\*J[1-4] ", l)).replace("**", "")


def load():
    return [json.loads(l) for l in SET.read_text(encoding="utf-8").splitlines() if l.strip()]


def make():
    eps = sorted(load(), key=lambda r: r["id"])
    order = list(range(len(eps)))
    random.Random(SHUFFLE_SEED).shuffle(order)
    items = [dict(pid=f"p-{n+1:03d}", id=eps[i]["id"], request=eps[i]["request"], stratum=eps[i]["stratum"],
                  form=eps[i]["form"], rule=eps[i]["rule"], violates=eps[i]["violates"], author=eps[i]["author"])
             for n, i in enumerate(order)]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "PRIVATE_key.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    head = ("You are an independent reader judging purchase requests against four rules written in words. "
            "An owner holds these rules for an automated purchasing assistant:\n\n" + rules_text() + "\n\n"
            "For each numbered request below, decide from the request text alone whether it breaks one of the four rules. "
            "Ignore prices, vendors and spending limits; judge only the rules above. Many requests break none. "
            "If one breaks more than one, give the clearest. Read the 'Not a violation' lines carefully.\n\n"
            "Reply with one JSON object per line and nothing else: {\"pid\":\"p-001\",\"verdict\":\"none\"} where verdict is "
            "\"none\", \"J1\", \"J2\", \"J3\" or \"J4\". Give a line for every request, in order.\n\nREQUESTS\n")
    for b in range(0, len(items), BATCH):
        body = "\n".join(f"{it['pid']}: {it['request']}" for it in items[b:b+BATCH])
        (OUT / f"judge_batch_{b//BATCH+1}.txt").write_text(head + body + "\n", encoding="utf-8")
    pool = [it["pid"] for it in items if it["stratum"] in ("fuzzy_only", "lookalike")]
    pick = sorted(random.Random(RICK_SEED).sample(pool, RICK_N))
    rq = {it["pid"]: it["request"] for it in items}
    sheet = ("Read each request and decide whether it breaks one of the four rules (from the request text alone; ignore prices and limits).\n\n"
             + rules_text() + "\n\nReply with one line per request: the p-number, then none, J1, J2, J3 or J4. Example: p-001 none\n\n"
             + "\n\n".join(f"{p}: {rq[p]}" for p in pick) + "\n")
    (OUT / "rick_sheet.txt").write_text(sheet, encoding="utf-8")
    for f in sorted(OUT.glob("*")):
        print(f.name, hashlib.sha256(f.read_bytes()).hexdigest())


def kappa(a, b):
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = collections.Counter(a), collections.Counter(b)
    pe = sum(ca[k] * cb[k] for k in ca) / n / n
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def read_reply(path):
    out = {}
    for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        l = l.strip()
        m = re.match(r"^\{.*\}$", l)
        if m:
            try:
                d = json.loads(l); out[d["pid"]] = d["verdict"]; continue
            except Exception:
                pass
        m = re.match(r"^(p-\d{3})[\s:,]+(none|J[1-4])\b", l, re.I)
        if m:
            out[m.group(1)] = m.group(2) if m.group(2).upper().startswith("J") is False else m.group(2).upper()
    return {k: ("none" if v.lower() == "none" else v.upper()) for k, v in out.items()}


def score(args):
    key = {it["pid"]: it for it in json.loads((OUT / "PRIVATE_key.json").read_text())}
    raters = {"author": {p: (it["rule"] if it["violates"] else "none") for p, it in key.items()}}
    for a in args:
        name, path = a.split("=", 1)
        raters[name] = read_reply(path)
    names = list(raters)
    print("raters:", {n: len(raters[n]) for n in names})
    for i, x in enumerate(names):
        for y in names[i+1:]:
            common = sorted(set(raters[x]) & set(raters[y]))
            bx = [raters[x][p] != "none" for p in common]; by = [raters[y][p] != "none" for p in common]
            kb = kappa(bx, by); kf = kappa([raters[x][p] for p in common], [raters[y][p] for p in common])
            agree = sum(a == b for a, b in zip(bx, by))
            print(f"{x} vs {y}: n={len(common)} agree(violates)={agree}/{len(common)} kappa(violates)={kb:.3f} kappa(rule)={kf:.3f}")
    print("\nDisagreements with the author label (pid, id, stratum, form, author, raters):")
    for p in sorted(key):
        votes = {n: raters[n].get(p) for n in names if p in raters[n]}
        if len({v != "none" for v in votes.values() if v}) > 1:
            it = key[p]
            print(p, it["id"], it["stratum"], it["form"], votes, "|", it["request"][:140].replace("\n", " "))
    cnt = collections.Counter()
    for p, it in key.items():
        for n in names[1:]:
            if p in raters[n]:
                cnt[(it["stratum"], n, (raters[n][p] != "none") == it["violates"])] += 1
    print("\nagreement with author by stratum:")
    for k in sorted(cnt): print(k, cnt[k])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["make", "score"]); ap.add_argument("rest", nargs="*")
    a = ap.parse_args()
    make() if a.cmd == "make" else score(a.rest)
