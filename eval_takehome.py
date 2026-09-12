"""
ABOUTME: Entry point for the Signal Labs attention-eval take-home. Exposes SignalEvaluator and a
ABOUTME: small CLI; the implementation lives in the signal_eval package next to this file.

Run locally:
    python eval_takehome.py            # 10-dossier demo
    python eval_takehome.py --all      # all dossiers, rule-count table

Design (see signal_eval/):
    spec.py        the specification transcribed into constants + the RULES table
    context.py     truth layer: cleaned telemetry, cohort baseline, indexes, cached NLI labels
    classifier.py  optional zero-shot NLI labeller for the spec points that are about meaning
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
LABEL_CACHE = Path("analysis/.cache/labels.json")


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
    evaluator = SignalEvaluator(label_cache_path=LABEL_CACHE if LABEL_CACHE.exists() else None)
    evaluator.load_context(_load("accounts.jsonl"), _load("owners.jsonl"), _load("telemetry.jsonl"),
                           _load("artifacts.jsonl"), dossiers)

    run_all = "--all" in sys.argv
    subset = dossiers if run_all else dossiers[:10]
    print(f"Evaluating {len(subset)} dossiers (classifier {'on' if evaluator.cx.classifier_active else 'off'})...")
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


if __name__ == "__main__":
    main()
