"""
ABOUTME: Agreement statistics the writeup quotes: pairwise kappa and PABAK on deserved_attention, Spearman on
ABOUTME: quality, and Krippendorff's alpha (interval and nominal) for the three annotators alone and with the evaluator.
"""

import csv
import json
import random
import sys
from itertools import combinations
from pathlib import Path

from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "analysis" / "runs"
FACTS = ROOT / "analysis" / "facts.csv"


def latest_run():
    runs = sorted(p for p in RUNS.glob("*.jsonl") if p.name != "manifest.jsonl")
    if not runs:
        sys.exit("no evaluator run under analysis/runs/. Run `python analysis/run_all.py` then `python analysis/facts.py`.")
    rows = [json.loads(l) for l in open(runs[-1])]
    return runs[-1].name, {r["signal_id"]: r["result"] for r in rows if "signal_id" in r}


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe != 1 else 0.0


def pabak(pairs):
    """Byrt, Bishop & Carlin 1993: 2*Po - 1, kappa with prevalence and bias effects removed."""
    return 2 * sum(a == b for a, b in pairs) / len(pairs) - 1


def boot_ci(pairs, fn, n_boot=2000, seed=7):
    rng = random.Random(seed)
    vals = sorted(fn([pairs[rng.randrange(len(pairs))] for _ in pairs]) for _ in range(n_boot))
    return vals[int(.025 * n_boot)], vals[int(.975 * n_boot)]


def krippendorff_alpha(units, metric):
    """Krippendorff 2004, alpha = 1 - Do/De. units = list of lists of values (one list per item, any raters).
    metric: 'interval' uses squared difference; 'nominal' uses 0/1 disagreement."""
    delta = (lambda a, b: (a - b) ** 2) if metric == "interval" else (lambda a, b: float(a != b))
    units = [u for u in units if len(u) >= 2]
    n = sum(len(u) for u in units)
    do = 0.0
    for u in units:
        m = len(u)
        do += sum(delta(a, b) for a, b in combinations(u, 2)) * 2 / (m - 1)
    do /= n
    allv = [v for u in units for v in u]
    de = sum(delta(a, b) for a, b in combinations(allv, 2)) * 2 / (n * (n - 1))
    return 1 - do / de if de else 1.0


def main():
    if not FACTS.exists():
        sys.exit("analysis/facts.csv not found. Run `python analysis/run_all.py` then `python analysis/facts.py` first.")
    run_name, ours = latest_run()
    rows = list(csv.DictReader(open(FACTS)))
    ann = {f"a{i}": {} for i in (1, 2, 3)}
    for r in rows:
        for i in (1, 2, 3):
            if r[f"ann{i}_deserved"] not in ("", "None"):
                ann[f"a{i}"][r["signal_id"]] = (r[f"ann{i}_deserved"] == "True", float(r[f"ann{i}_quality"]))
    print(f"run: {run_name}")

    print("\n1. deserved_attention — pairwise kappa and PABAK (bootstrap CI, 2000 row resamples)")
    raters = {k: {s: v[0] for s, v in d.items()} for k, d in ann.items()}
    raters["evaluator"] = {s: bool(r["deserved_attention"]) for s, r in ours.items()}
    for x, y in combinations(["a1", "a2", "a3", "evaluator"], 2):
        common = sorted(set(raters[x]) & set(raters[y]))
        pairs = [(raters[x][s], raters[y][s]) for s in common]
        lo, hi = boot_ci(pairs, kappa)
        raw = sum(a == b for a, b in pairs) / len(pairs)
        print(f"  {x:9s} vs {y:9s} n={len(pairs):3d} raw={raw:.3f} kappa={kappa(pairs):+.3f} [{lo:+.3f},{hi:+.3f}] PABAK={pabak(pairs):+.3f}")

    print("\n2. quality_score — Spearman rank correlation")
    qual = {k: {s: v[1] for s, v in d.items()} for k, d in ann.items()}
    qual["evaluator"] = {s: float(r["quality_score"]) for s, r in ours.items()}
    for x, y in combinations(["a1", "a2", "a3", "evaluator"], 2):
        common = sorted(set(qual[x]) & set(qual[y]))
        rho = spearmanr([qual[x][s] for s in common], [qual[y][s] for s in common]).statistic
        print(f"  {x:9s} vs {y:9s} n={len(common):3d} rho={rho:+.3f}")

    print("\n3. quality_score — Krippendorff's alpha over the 130 dossiers all three annotators rated")
    triple = sorted(set(qual["a1"]) & set(qual["a2"]) & set(qual["a3"]))
    humans = [[qual[a][s] for a in ("a1", "a2", "a3")] for s in triple]
    panel = [[qual[a][s] for a in ("a1", "a2", "a3", "evaluator")] for s in triple]
    for metric in ("interval", "nominal"):
        print(f"  {metric:8s} humans alone={krippendorff_alpha(humans, metric):.3f}   "
              f"humans + evaluator={krippendorff_alpha(panel, metric):.3f}")
    print(f"  (n={len(triple)} dossiers; nominal treats 0.62 vs 0.63 as total disagreement, interval by distance)")


if __name__ == "__main__":
    main()
