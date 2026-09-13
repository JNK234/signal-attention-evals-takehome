"""
ABOUTME: Entry point for the Signal Labs attention-eval take-home. Exposes SignalEvaluator and a
ABOUTME: small CLI; the implementation lives in the signal_eval package next to this file.

Run locally:
    python eval_takehome.py            # 10-dossier demo
    python eval_takehome.py --all      # all dossiers, rule-count table

Design (see signal_eval/):
    spec.py        the specification transcribed into constants + the RULES table
    context.py     truth layer: cleaned telemetry, cohort baseline, indexes, cached NLI labels
    text.py        block layer: quote depth + signature tagging, nothing discarded
    labels.py      hypothesis sentences per label, per-model threshold bands, decide()
    labellers/     engines behind one contract (NLI, cache, table double) and resolve()
    checks/        one module per rule group; each check is (dossier, ctx, cx) -> [violation]
    scoring.py     layer 2: quality / risk / deserved (placeholder rubric, defined from the facts)
    evaluator.py   SignalEvaluator wiring the above together
"""

import json
import sys
from collections import Counter
from pathlib import Path

from signal_eval import RULES, SignalEvaluator  # noqa: F401  (re-exported for the grader)

DATA = Path("data")
LABEL_CACHE = None   # default: analysis/.cache/labels_<model-slug>.json (labellers.default_cache_path)


def _load(name):
    path = DATA / name
    if not path.exists():
        return None
    with open(path) as f:
        return [json.loads(line) for line in f]


def main():
    dossiers = _load("signal_dossiers.jsonl")
    if dossiers is None:
        print("No data found. Make sure data/signal_dossiers.jsonl exists.")
        return
    evaluator = SignalEvaluator(label_cache_path=LABEL_CACHE)
    evaluator.load_context(_load("accounts.jsonl"), _load("owners.jsonl"), _load("telemetry.jsonl"),
                           _load("artifacts.jsonl"), dossiers)

    run_all = "--all" in sys.argv
    subset = dossiers if run_all else dossiers[:10]
    print(f"Evaluating {len(subset)} dossiers (classifier {'on' if evaluator.cx.classifier_active else 'off'}: "
          f"{evaluator.cx.classifier_reason})...")
    counts, results = Counter(), []
    for d in subset:
        r = evaluator.evaluate(d)
        results.append(r)
        counts.update({v["rule"] for v in r["violations"]})
        if not run_all:
            print(f"  {d['signal_id']}: quality={r['quality_score']:.2f}, risk={r['risk_score']:.2f}, "
                  f"deserved={r['deserved_attention']}, violations={len(r['violations'])}")
    if run_all:
        print(f"\n{'rule':<5}{'dossiers':>9}  spec reference")
        for rid, ref, _, _ in RULES:
            print(f"{rid:<5}{counts.get(rid, 0):>9}  {ref}")
    print(f"\nEvaluated {len(results)} dossiers.")
    # evaluate() only persists labels in batches (README l.226: no per-call side effects); write the tail now.
    try:
        evaluator.cx.flush_cache()
    except OSError as exc:
        print(f"label cache not written: {exc!r}")


if __name__ == "__main__":
    main()
