"""The F1 prompt format and output parser, copied unchanged from the project's trainer (flagger/train_eval.py).

The trainer and the case generator are not part of this kit. This file holds only what `run_flaggers.py` needs
to build the F1 prompt and to read its one-line output. Running F1 itself also needs the LoRA adapter, which
is not released here; F2 and F3, `judgment_pipeline combine` and the tests run without it.
"""
import os

BASE = os.environ.get("FLAGGER_BASE", "Qwen/Qwen2.5-1.5B-Instruct")  # the variable exists only for tests
CONCEPTS = ("NO_STRUCTURING", "NO_CONFLICT_OF_INTEREST", "NO_SECRECY_OR_PRESSURE", "BUSINESS_USE_ONLY")
SYSTEM = "You check purchase requests for an automated purchasing assistant. Reply with one line."


def user_text(case):
    return case["text"] + "".join(f"\nNOTE: {n}" for n in case.get("notes", []))


def parse_output(s):
    """-> (flagged, concept or None, valid). A flag with an unknown name counts as flagged but not concept-correct."""
    line = (s or "").strip().splitlines()[0].strip() if (s or "").strip() else ""
    if line == "OK": return False, None, True
    if line.startswith("FLAG"):
        parts = line.split()
        return True, (parts[1] if len(parts) > 1 and parts[1] in CONCEPTS else None), True
    return False, None, False
