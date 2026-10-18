"""Run the sealed independent sets on a commercial model through the Anthropic API.

    python -m exp5.sim.api_run purchasing --model claude-sonnet-5-5 --set ... --sha ... [--notes wrapped]
    python -m exp5.sim.api_run delegation --model claude-sonnet-5-5 --set ... --sha ... [--claims withheld]

Everything else (episodes, arms, executors, judging, report, DEVIATIONS guard, one run per model
and mode) is the existing runner, unchanged. This module only swaps the model client and fetches
the model's answers in parallel first, so the sequential runner then reads them from the cache.
"""
from __future__ import annotations
import sys
from concurrent.futures import ThreadPoolExecutor

from . import delegation_independent as DI
from . import delegation_llm as DL
from . import llm_runner as LR
from . import purchasing_independent as PI
from .api_client import AnthropicClient

WORKERS = 6


def _prefetching(orig, build):
    def run(eps, clients, primes, withheld=None, progress=None):
        jobs = [(client, build(arm, ep, primes)) for arm, client in clients.items() for ep in eps]
        done = [0]

        def one(j):
            j[0].chat(j[1], None)
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"\rfetched {done[0]}/{len(jobs)}", end="", flush=True)
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(one, jobs))
        print()
        return orig(eps, clients, primes, withheld, progress) if withheld is not None else orig(eps, clients, primes, progress=progress)
    return run


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("purchasing", "delegation"):
        sys.exit("usage: python -m exp5.sim.api_run purchasing|delegation [runner options]")
    which, rest = argv[0], argv[1:]
    mod = PI if which == "purchasing" else DI
    mod.OllamaClient = AnthropicClient
    lib = LR if which == "purchasing" else DL
    lib.run_episodes = _prefetching(lib.run_episodes, lib.build_messages)
    return mod.main(rest)


if __name__ == "__main__":
    sys.exit(main())
