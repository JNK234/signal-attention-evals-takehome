"""
ABOUTME: Reads every artefact in the corpus through Context.read_artifact with the NLI labeller, filling the
ABOUTME: block-score cache so evaluate() and the account-level trigger scan never need the model at run time.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval.context import Context  # noqa: E402
from signal_eval.labellers import default_cache_path  # noqa: E402


def main(limit=None):
    t0 = time.time()
    cx = Context(labeller="nli")
    cx.accounts = {a["account_id"]: a for a in E._load("accounts.jsonl")}
    arts = E._load("artifacts.jsonl")
    if limit:
        arts = arts[:limit]
    n_before = len(cx.cache)
    cx._classifier_ready()
    t1 = time.time()
    print(f"model {cx.labeller.model_id}: active={cx.classifier_active} ({cx.classifier_reason}) in {t1 - t0:.0f}s; "
          f"{n_before} cached scores")
    unreadable, hist, truncated = 0, 0, 0
    for i, a in enumerate(arts, 1):
        r = cx.read_artifact(a)
        unreadable += bool(r["unreadable"])
        hist += bool(r["historical"])
        truncated += r["truncated"]
        cx.save_cache()
        if i % 100 == 0:
            print(f"  {i}/{len(arts)} artefacts, {(time.time() - t1) / i:.2f}s each", flush=True)
    cx.flush_cache()
    dt = time.time() - t1
    print(f"read {len(arts)} artefacts in {dt:.0f}s ({dt / max(len(arts), 1):.2f}s each); scores {n_before} → {len(cx.cache)}; "
          f"unreadable {unreadable}, with historical labels {hist}, truncated {truncated} → {default_cache_path(cx.labeller.model_id)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
