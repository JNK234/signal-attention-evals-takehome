"""
ABOUTME: Labels every non-bot artefact in the corpus with the NLI classifier through the Context's
ABOUTME: text-hash cache, so account-level trigger scans (not just attached evidence) are possible.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval.context import Context  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".cache" / "labels.json"


def main():
    t0 = time.time()
    cx = Context(label_scope="all", label_cache_path=CACHE)
    n_before = len(cx._labels)
    cx.load(E._load("accounts.jsonl"), E._load("owners.jsonl"), [], E._load("artifacts.jsonl"), E._load("signal_dossiers.jsonl"))
    print(f"labels: {n_before} cached → {len(cx._labels)} total in {time.time()-t0:.0f}s; classifier_active={cx.classifier_active}; "
          f"accounts with triggers: {len(cx.account_triggers)} → {CACHE}")


if __name__ == "__main__":
    main()
