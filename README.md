# Governance-Agents

A starter kit for people who put hard rules and fuzzy rules around AI agents. It holds the code, the sealed test sets, and the raw results behind the study "Dependable Agents Need Hard and Fuzzy Rules" (Roth, 2026).

The idea in one paragraph. A **hard layer** (the guard in `exp5/gate`) decides what an agent may execute. It checks exact facts: money, vendors, quantities, approvals, tokens. It does not read intent. A **fuzzy layer** (a flagger) reads the request for what exact checks cannot see, such as structuring a purchase to stay under a limit, a conflict of interest, secrecy, or personal use. The flagger can only narrow: a flagged purchase becomes "request approval". The guard still decides. The study measures what each layer catches alone and together, on episodes written by an outside author and sealed before any model saw them.

## What is here

| Folder | Contents |
|---|---|
| `exp5/gate/` | The guard: money, ledger, approvals, packs, data-access rules. |
| `exp5/sim/` | The simulated world, proposers, runners, and the end-to-end study (`judgment_pipeline.py`). |
| `exp5/flagger/` | Flagger prompt (F2/F3), the F1 instilling specification, F1 format shim, `run_flaggers.py`, overlap check. |
| `exp5/author/` | Author prompts and validators for the sealed sets, and the adjudication tool. |
| `exp5/prereg/independent/` | The four sealed sets, the raw author replies, the adjudication packets, replies, and answers. |
| `exp5/prereg/DEVIATIONS_governance.md` | The part of the project's deviations log that concerns this study. Each sealed set's hash is recorded here before any run. |
| `exp5/data/` | Raw outputs: proposals, flags, run results, and the report of the end-to-end study. |
| `exp5/tests/` | 200+ tests. |

## Sealed sets

Check these before you run anything. The runners stop if the hash differs.

| Set | Episodes | sha256 (first 16) |
|---|---|---|
| `purchasing_set_a1_v2.jsonl` | purchasing | 21b7600c69877005 |
| `delegation_set_a1_v1.jsonl` | delegation | 5fba0db601f5a3e5 |
| `dataaccess_set_a1_v1.jsonl` | data access | 5852181b14cddc38 |
| `judgment_set_a1_v1.jsonl` | 300 judgment-rule episodes | ec7254248c22f5aa |

`sha256sum exp5/prereg/independent/*.jsonl` prints the full values. `SHA256SUMS` lists the hash of every data file in this kit.

## Reproduce the end-to-end analysis

Needs Python 3.10 or newer and no model.

    pip install pytest hypothesis
    python3 -m pytest -q exp5/tests
    python3 -m exp5.sim.judgment_pipeline combine \
      --proposals exp5/data/judgment_proposals_llama3.2.jsonl exp5/data/judgment_proposals_claude-sonnet-5-5.jsonl \
      --flags exp5/data/judgment_flags_F1.jsonl exp5/data/judgment_flags_F2.jsonl exp5/data/judgment_flags_F3.jsonl \
      --out my_results.json

`my_results.json` is identical to `exp5/data/judgment_results.json`. The printed report lists the seven predictions made before the run, and states which held and which did not.

## Reproduce the conformance test of the hard layer

This is the test behind Table 2 of the paper. It needs no model: an adversary builds every proposal it
can from each episode's own parts and runs all of them through the guard, so the result does not depend
on which model proposes. Each run checks the set's hash against the sealed value recorded in the
deviations log and refuses to overwrite an existing output, so give each run its own `--out` file.

    # Purchasing
    python3 -m exp5.sim.purchasing_independent --adversary \
      --set exp5/prereg/independent/purchasing_set_a1_v2.jsonl \
      --sha 21b7600c69877005a908c90f8c9b5c592e6fb2eeb033bb5ce61d63ff6e9bdbb3 --out my_purchasing.json

    # Data access
    python3 -m exp5.sim.dataaccess_independent --adversary \
      --set exp5/prereg/independent/dataaccess_set_a1_v1.jsonl \
      --sha 5852181b14cddc3868874b25e2cc8ff53b0f392b5530a1268d872f9387a06809 --out my_dataaccess.json

    # Policy independence: the owner's rules come from an outside brief, before and after the repair
    python3 -m exp5.sim.purchasing_independent --adversary \
      --set exp5/prereg/independent/policy_independence_set_b1_v1.jsonl \
      --sha 389cf6e9efaa530329b25fbb27bb68819c30462115f9a7b91d8b26bba2d0502b \
      --rules exp5/prereg/policy_independence/rules_brief_v1.yaml --out my_b1_v1.json
    python3 -m exp5.sim.purchasing_independent --adversary \
      --set exp5/prereg/independent/policy_independence_set_b1_v1.jsonl \
      --sha 389cf6e9efaa530329b25fbb27bb68819c30462115f9a7b91d8b26bba2d0502b \
      --rules exp5/prereg/policy_independence/rules_brief_v2.yaml --out my_b1_v2.json

| Run | Episodes | Proposals | Unsafe, no guard | Unsafe, with the guard | Bound per episode |
|---|---:|---:|---:|---:|---:|
| Purchasing | 340 | 58,832 | 32,314 | 0 | 0.88% |
| Data access | 530 | 124,372 | 122,982 | 0 | 0.56% |
| Policy independence, brief's rules | 340 | 77,387 | 50,267 | 2, at exactly $475.00 | 0.07% to 2.1% (two-sided) |
| Policy independence, after the repair | 340 | 77,387 | 50,267 | 0 | diagnostic only |

The shipped results are in `exp5/data/independent_purchasing_adversary.json`,
`independent_dataaccess_adversary.json`, `policy_independence_b1_before_repair.json` and
`policy_independence_b1_after_repair_diagnostic.json`. Each run takes one to a few minutes.

**Policy independence.** A model wrote a plain-language owner brief from an interview
(`exp5/prereg/policy_independence/Owner_Policy_Brief_v1.md`). A fixed mapping turned its seven numbers into
`rules_brief_v1.yaml` (see the mapping record in the same folder), and a second model, in a fresh session,
wrote 340 episodes from the brief alone. Where the brief and the guard could differ was written down before
any episode existed (deviations entry 34). They differed at exactly $475.00: two episodes executed where the
brief requires approval, and one was refused where the brief allows it. The repair is the owner setting
`hug_inclusive` on `Limits` (`rules_brief_v2.yaml` sets it). The clean rerun does not confirm the repair,
because the repair was made after the set was seen. The original runner's two reports are kept as produced
in the same folder.

**Delegation** has no proposal search by design: authority there is a signed chain, so the guard is tested
by replaying all 470 episodes under every prompt condition and by property tests over generated chains
(`exp5/tests/test_delegation.py`, `test_properties.py`).

## Author prompts

Every sealed set was written by an outside author from a prompt in `exp5/author/`: purchasing
(`Author_Prompt_Purchasing_v3.md` and `ChatGPT_Author_Prompt_Purchasing_v1.md`), delegation
(`Author_Prompt_Delegation_v1.md`), data access (`Author_Prompt_DataAccess_v1.md`), the judgment rules
(`Author_Prompt_Judgment_v1.md`) and policy independence (`Author_Prompt_Purchasing_v4_Independent_Ready.md`).
Deviations entry 57 says which prompt each author received and which hashes were recorded before use.
The models, versions and settings of every run are named in the deviations log.

## Run it on your own models

- Proposers: `python3 -m exp5.sim.judgment_pipeline propose` (Ollama by default, `--api` for Claude). It writes once per model and refuses to overwrite without `--rerun`.
- Flaggers: `python3 -m exp5.flagger.run_flaggers --flagger F2` (local base model with the flagger prompt) or `--flagger F3` (API model).
- F1 needs the LoRA adapter, which is not released here. F1's flags are in `exp5/data/judgment_flags_F1.jsonl`.

## Limits you should know

- The world is simulated and the arena is one purchasing assistant.
- The authors of the sealed sets saw the rules as text. The label (rule-breaking or legitimate) is subjective, though adjudicated; see `exp5/prereg/independent/adjudication/`.
- The F1 case generator was written after the sealed set was read. The trainer and generator are not in this kit.
- Each condition ran once. Nothing was tuned on the sealed sets.
- The policy-independence test used one brief, one brief writer and one episode author, and the mapping from brief to rules is fixed. A different brief could expose a different gap.
- A hard layer cannot cover actions it does not model. The study does not test that.

## Relation to other repositories

This kit was cut from the working repository `safety-concept-experiment`. It leaves out work on other studies (wellness detectors, early concept sets) and the Concept Programming training code.

## License

PolyForm Noncommercial License 1.0.0. See `LICENSE`. Copyright 2026 Frederick Roth.

You may use, copy, modify and share this kit for personal, research, educational and other noncommercial purposes, and charities, schools, public research bodies and government institutions may use it for their work. Any commercial use is not permitted. For commercial use, contact the author.

This is a source-available license, not an open-source license in the sense of the Open Source Definition.
