"""Run one flagger on the sealed judgment set and save its flags (stage 2 of the end-to-end study; DEVIATIONS 42 and 51). Written by Claude.

The flagger reads the requester's words and any notes, nothing else (not prices, quotes or limits), and answers OK or FLAG <RULE>. Only a valid FLAG line counts as a flag.
  F1  Qwen2.5-1.5B-Instruct plus the Concept-Programming adapter, in the same format it was trained on (short system line).
  F2  the same base model, no adapter, given Flagger_Prompt_v1.txt (the four rules in words) as its system prompt.
  F3  Sonnet 5.5 through the API, given Flagger_Prompt_v1.txt.
The sealed set is read only after its sha256 matches and appears in DEVIATIONS.md. One run per flagger; an existing output stops the run.

  python3 -m exp5.flagger.run_flaggers --flagger F1 --adapter exp5/flagger/runs/round1/ckpt/epoch_3
  python3 -m exp5.flagger.run_flaggers --flagger F2
  python3 -m exp5.flagger.run_flaggers --flagger F3 --model claude-sonnet-5-5
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..sim.judgment_pipeline import DEVIATIONS, SEALED_SHA, SET_PATH, load_sealed
from .f1_format import BASE, SYSTEM as F1_SYSTEM, parse_output, user_text

PROMPT_PATH = Path(__file__).with_name("Flagger_Prompt_v1.txt")
PROMPT_SHA_PREFIX = "871f1045"


def prompt_text() -> str:
    raw = PROMPT_PATH.read_bytes()
    if not hashlib.sha256(raw).hexdigest().startswith(PROMPT_SHA_PREFIX):
        sys.exit(f"STOP: {PROMPT_PATH.name} does not match the fixed prompt (sha256 starts {PROMPT_SHA_PREFIX}); the prompt may not change after the set is sealed")
    return raw.decode("utf-8").strip()


def case_of(rec: dict) -> dict:
    return {"text": rec["request"], "notes": list(rec["notes"])}


def generate_local(system: str, cases: list, adapter: str | None, batch: int = 8, max_new: int = 24) -> list:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device == "cuda" else torch.bfloat16
    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(BASE, dtype=dtype).to(device)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter).to(device)
    model.eval()
    outs = []
    for b in range(0, len(cases), batch):
        chunk = cases[b:b + batch]
        prompts = [tok.apply_chat_template([{"role": "system", "content": system}, {"role": "user", "content": user_text(c)}], tokenize=False, add_generation_prompt=True) for c in chunk]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to(device)
        with torch.no_grad():
            g = model.generate(**enc, max_new_tokens=max_new, do_sample=False, pad_token_id=tok.pad_token_id)
        outs += [tok.decode(x[enc["input_ids"].shape[1]:], skip_special_tokens=True).strip() for x in g]
        print(f"\r{len(outs)}/{len(cases)}", end="", flush=True)
    print()
    return outs


def generate_api(system: str, cases: list, model: str, cache: str, workers: int = 6) -> list:
    from ..sim.api_client import AnthropicClient
    client = AnthropicClient(model, cache_dir=cache, max_tokens=60)

    def one(c):
        r = client.chat([{"role": "system", "content": system}, {"role": "user", "content": user_text(c)}], None)
        return (r.get("raw_text") or r.get("content") or "").strip()
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, cases))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flagger", required=True, choices=("F1", "F2", "F3"))
    ap.add_argument("--adapter", default="exp5/flagger/runs/round1/ckpt/epoch_3")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--set", default=SET_PATH)
    ap.add_argument("--sha", default=SEALED_SHA)
    ap.add_argument("--deviations", default=DEVIATIONS)
    ap.add_argument("--cache", default="exp5/data/cache")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rerun", action="store_true")
    a = ap.parse_args(argv)
    out = a.out or f"exp5/data/judgment_flags_{a.flagger}.jsonl"
    if os.path.exists(out) and not a.rerun:
        sys.exit(f"STOP: {out} exists; the sealed set is run once per flagger. A rerun needs its own DEVIATIONS entry and --rerun")
    recs = load_sealed(a.set, a.sha, a.deviations)
    cases = [case_of(r) for r in recs]
    if a.flagger == "F1":
        if not os.path.isdir(a.adapter):
            sys.exit(f"STOP: adapter folder {a.adapter} not found")
        raws = generate_local(F1_SYSTEM, cases, a.adapter)
    elif a.flagger == "F2":
        raws = generate_local(prompt_text(), cases, None)
    else:
        raws = generate_api(prompt_text(), cases, a.model, a.cache)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp"
    nflag = ninvalid = 0
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {"flagger": a.flagger, "sha": a.sha, "adapter": a.adapter if a.flagger == "F1" else None, "model": a.model if a.flagger == "F3" else BASE,
                                      "date": datetime.date.today().isoformat()}}) + "\n")
        for r, raw in zip(recs, raws):
            flagged, concept, valid = parse_output(raw)
            nflag += flagged
            ninvalid += not valid
            f.write(json.dumps({"id": r["id"], "output": raw, "flagged": flagged, "concept": concept, "valid": valid}) + "\n")
    os.replace(tmp, out)
    print(f"saved {out}: {nflag} of {len(recs)} flagged, {ninvalid} invalid outputs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
