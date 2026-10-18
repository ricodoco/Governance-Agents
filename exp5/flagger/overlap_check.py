"""Fail a flagger training file if any case overlaps the sealed judgment set.
  python3 -m exp5.flagger.overlap_check TRAIN.jsonl [--out dropped_ids.txt] [--clean TRAIN.clean.jsonl]
A case fails if it shares a six-word sequence with a sealed request (digits -> #), or if its word-set Jaccard with one is >= 0.7.
The sealed set is read only for this check."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

SEALED = Path("exp5/prereg/independent/judgment_set_a1_v1.jsonl")
N, J = 6, 0.7


def toks(s):
    return re.findall(r"[a-z#']+", re.sub(r"\d+", "#", s.lower()))


def grams(t, n=N):
    return {tuple(t[i:i+n]) for i in range(len(t) - n + 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("train"); ap.add_argument("--out"); ap.add_argument("--clean", help="write the file without the failing cases")
    a = ap.parse_args(argv)
    sealed = [json.loads(l)["request"] for l in SEALED.read_text(encoding="utf-8").splitlines() if l.strip()]
    st = [toks(s) for s in sealed]; sg = set().union(*[grams(t) for t in st]); ss = [set(t) for t in st]
    bad = []; n = 0
    for l in open(a.train, encoding="utf-8"):
        if not l.strip(): continue
        r = json.loads(l); n += 1
        t = toks(r["text"]); why = None
        if grams(t) & sg: why = "shares a six-word sequence"
        else:
            s = set(t)
            if s and any(len(s & x) / len(s | x) >= J for x in ss): why = "word overlap >= 0.7"
        if why: bad.append((r.get("id"), why))
    print(f"checked {n} cases: {len(bad)} overlap the sealed set")
    for i, w in bad[:50]: print(" ", i, w)
    if a.out: Path(a.out).write_text("\n".join(i for i, _ in bad) + "\n")
    if a.clean:
        drop = {i for i, _ in bad}
        rows = [l for l in open(a.train, encoding="utf-8") if l.strip() and json.loads(l).get("id") not in drop]
        Path(a.clean).write_text("".join(rows), encoding="utf-8"); print(f"wrote {len(rows)} clean cases to {a.clean}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
