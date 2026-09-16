"""
ABOUTME: Candidate risk conditions for risk_score v2, selected by spec reasoning alone and then
ABOUTME: reported against BOTH label sources (outcomes and annotator flags) on dev and held-out.

Selection rule, fixed before any number was read: a condition earns a place only if the specification
itself forbids the thing it fires on. Outcomes and annotator flags are reported, never optimised
against — scoring.py already states the corpus is kept for validation, as the annotators are.

The gap this addresses: docs/domain.md names three harms — a complaint, a wasted CSM slot, a missed
renewal. The shipped table carries mechanisms for complaints (§8.2/§8.3/§8.4/§8.7, VIS_UNDESERVED) and
for missed renewals (§8.1, UNROUTED_DESERVED), but only one for a wasted slot (M6). 78 of the 203
dossiers the annotators call wasted_attention score exactly 0.0.

Spec authority for each candidate, quoted:
  M3 "A routed signal must be material ... materiality_floor <= arr_at_risk <= arr_annual"
  M4 "Below the floor, re-scope or suppress ... It must not route it as-is."
  M5 "Once the agent states an arr_at_risk, every later reference ... must be consistent ...
      a twelve-fold restatement is a bug, not a revision"
  M6 "... it is the most common way this system wastes attention." (already shipped)
Section 11 rates every M rule "Materiality or grounding error — High".
"""

import glob
import itertools
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Shipped table, unchanged. judgment stays 0.30 as shipped.
SHIPPED = {"§8.2": .45, "§8.3": .45, "I6": .45, "§8.4": .45, "§8.7": .45, "§8.1": .45,
           "M6": .20, "VIS_UNDESERVED": .30, "UNROUTED_DESERVED": .30}

# Candidates. Every one is a rule the spec states as a prohibition, weighted at the tier
# spec §11 assigns it. No candidate is included because it correlated with anything.
CANDIDATES = {
    # "It must not route it as-is." The floor is defined as "the minimum exposure that justifies
    # spending a human's time on this account", so routing below it spends time the spec says is
    # not justified — a wasted slot by the spec's own definition of the floor.
    "M4_ROUTED": (.20, "spec §9 M4 routed below the materiality floor the spec defines as the "
                       "minimum exposure justifying a human's time"),
    # M3 is the same obligation stated positively and is a separate check in the evaluator.
    "M3_ROUTED": (.20, "spec §9 M3 routed signal outside materiality_floor <= arr_at_risk <= arr_annual"),
    # "a twelve-fold restatement is a bug, not a revision" — the reader is acting on a figure the
    # dossier contradicts elsewhere. The exposure that justified the slot is not the stated one.
    "M5_ROUTED": (.20, "spec §9 M5 arr_at_risk restated inconsistently in a dossier a human read"),
    # M1: "A signal claiming more exposure than the contract is worth is arithmetically wrong."
    "M1_ROUTED": (.20, "spec §9 M1 arr_at_risk exceeds arr_annual in a dossier a human read"),
}


def load():
    run = sorted(glob.glob(str(ROOT / "analysis/runs/2026*.jsonl")))[-1]
    rows = [json.loads(l) for l in open(run)][1:]
    R = {r["signal_id"]: r for r in rows}
    out = {}
    for l in open(ROOT / "data/outcomes.jsonl"):
        o = json.loads(l)
        out[o["signal_id"]] = o
    ann = defaultdict(list)
    for i in (1, 2, 3):
        for l in open(ROOT / f"data/annotations/annotator_{i}.jsonl"):
            a = json.loads(l)
            ann[a["signal_id"]].append(a)
    split = json.load(open(ROOT / "analysis/.cache/risk_split.json"))
    return R, out, ann, split


def rules_of(r):
    return {v["rule"] for v in r["result"]["violations"] if v["rule"] != "UNEVALUATED"}


def fires(cid, r):
    f = r["facts"] or {}
    v = rules_of(r)
    if not f.get("reached_human"):
        return False
    return {"M4_ROUTED": "M4", "M3_ROUTED": "M3", "M5_ROUTED": "M5", "M1_ROUTED": "M1"}[cid] in v


def score(r, combo):
    q = 1.0
    for cid in (r["facts"] or {}).get("risk_conditions") or []:
        q *= 1 - SHIPPED[cid]
    for cid in combo:
        if fires(cid, r):
            q *= 1 - CANDIDATES[cid][0]
    return round(1 - q, 3)


def auc(sc, lab):
    """Probability a positive outranks a negative. 0.5 = chance, 1.0 = perfect. Ties count half."""
    pos = [sc[s] for s in sc if lab.get(s)]
    neg = [sc[s] for s in sc if not lab.get(s)]
    if not pos or not neg:
        return None
    w = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return w / (len(pos) * len(neg))


def maj_flag(ann, s, flag):
    v = ann.get(s) or []
    if not v:
        return None
    n = sum(1 for a in v if flag in (a.get("risk_flags") or []))
    return n > 0 and n * 2 >= len(v)


def report(combo, R, out, ann, ids, label):
    sc = {s: score(R[s], combo) for s in ids}
    wasted = {s: (out.get(s) or {}).get("escalation_was_wasted") is True for s in ids}
    compl = {s: (out.get(s) or {}).get("customer_complained_about_outreach") is True for s in ids}
    hw = {s: maj_flag(ann, s, "wasted_attention") for s in ids if maj_flag(ann, s, "wasted_attention") is not None}
    scl = {s: sc[s] for s in hw}
    zeros = sum(1 for x in sc.values() if x == 0.0)
    # harm sitting at exactly zero — the acceptance criterion, per commit 5904b0a's shape
    hz = sum(1 for s in ids if sc[s] == 0.0 and (wasted[s] or compl[s]))
    return dict(label=label, n=len(ids), zeros=zeros, pct0=zeros / len(ids), distinct=len(set(sc.values())),
                harm_at_zero=hz, auc_w=auc(sc, wasted), auc_c=auc(sc, compl), auc_h=auc(scl, hw))


def line(m):
    return (f"{m['label']:<34}{m['n']:>5}{m['zeros']:>7}{m['pct0']:>8.1%}{m['distinct']:>6}"
            f"{m['harm_at_zero']:>7}{m['auc_w']:>9.3f}{m['auc_c']:>9.3f}"
            f"{(m['auc_h'] if m['auc_h'] is not None else float('nan')):>9.3f}")


HDR = (f"{'combination':<34}{'n':>5}{'zeros':>7}{'%0':>8}{'dist':>6}{'harm@0':>7}"
       f"{'AUCwast':>9}{'AUCcompl':>9}{'AUChum':>9}")


def main():
    R, out, ann, split = load()
    dev = [s for s in split["dev"] if s in R]

    print("Selection: spec prohibition only. Outcomes and annotator flags are REPORTED, not optimised.\n")
    print("=== DEV (n=%d) — all 15 non-empty subsets of 4 candidates ===" % len(dev))
    print(HDR)
    print(line(report([], R, out, ann, dev, "(shipped, no candidate)")))
    results = []
    for k in range(1, len(CANDIDATES) + 1):
        for combo in itertools.combinations(CANDIDATES, k):
            m = report(list(combo), R, out, ann, dev, "+".join(c.replace("_ROUTED", "") for c in combo))
            results.append((combo, m))
    for combo, m in results:
        print(line(m))

    print("\n=== candidate firing counts (whole corpus) ===")
    for cid in CANDIDATES:
        n = sum(1 for s in R if fires(cid, R[s]))
        print(f"  {cid:<12} fires on {n:>4} of 629   {CANDIDATES[cid][1]}")


if __name__ == "__main__":
    main()
