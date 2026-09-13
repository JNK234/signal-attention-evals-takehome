"""
ABOUTME: Diffs two saved runs (see signal_eval/runlog.py): rule-count deltas, per-rule severity histograms,
ABOUTME: M6 claim-status split, deserved_attention flips with reasons, trigger kinds, annotator agreement.
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from signal_eval import RULES  # noqa: E402
from signal_eval.runlog import load_run  # noqa: E402

ANNOTATIONS = Path(__file__).resolve().parents[1] / "data" / "annotations"


def by_id(rows):
    return {r["signal_id"]: r for r in rows}


def rule_counts(rows):
    """Dossiers with ≥1 violation of each rule (the run_all.py table)."""
    c = Counter()
    for r in rows:
        c.update({v["rule"] for v in r["result"]["violations"]})
    return c


def severity_hist(rows):
    """rule -> Counter(severity -> number of violations)."""
    h = {}
    for r in rows:
        for v in r["result"]["violations"]:
            h.setdefault(v["rule"], Counter())[v["severity"]] += 1
    return h


def claim_split(rows):
    return Counter(s for r in rows for s in r["facts"].get("claim_status", []))


def trigger_kinds(rows):
    return Counter(t for r in rows for t in r["facts"].get("triggers", []))


def trigger_split(rows):
    """Dossiers by trigger provenance: confirmed / uncertain-only / unattached-only (the account index) / none.
    Runs saved before the split existed have no confirmed/uncertain keys and count as 'unsplit'."""
    c = Counter()
    for r in rows:
        f = r["facts"]
        s = f.get("trigger_source") or {}
        if "confirmed" not in s and "uncertain" not in s:
            c["unsplit (older run)" if f.get("triggers") else "none"] += 1
        elif s.get("confirmed"):
            c["confirmed"] += 1
        elif s.get("uncertain"):
            c["uncertain only"] += 1
        elif f.get("account_triggers_unattached"):
            c["unattached only"] += 1
        else:
            c["none"] += 1
    return c


def annotator_agreement(rows):
    """annotator -> (agree, n) on deserved_attention, over the dossiers that annotator labelled."""
    out = {}
    if not ANNOTATIONS.is_dir():
        return out
    ids = by_id(rows)
    for path in sorted(ANNOTATIONS.glob("annotator_*.jsonl")):
        with open(path) as f:
            ann = [json.loads(line) for line in f]
        labelled = [a for a in ann if a["signal_id"] in ids]
        agree = sum(1 for a in labelled if a["deserved_attention"] == ids[a["signal_id"]]["result"]["deserved_attention"])
        out[path.stem] = (agree, len(labelled))
    return out


def fmt_delta(a, b):
    return f"{a:>5} → {b:<5} ({b - a:+d})" if a != b else f"{a:>5} → {b:<5}"


def main(path_a, path_b):
    meta_a, rows_a = load_run(path_a)
    meta_b, rows_b = load_run(path_b)
    print(f"A: {Path(path_a).name}  sha={meta_a.get('git_sha')}  model={meta_a.get('model_id')}  n={meta_a.get('n')}")
    print(f"B: {Path(path_b).name}  sha={meta_b.get('git_sha')}  model={meta_b.get('model_id')}  n={meta_b.get('n')}")
    ida, idb = by_id(rows_a), by_id(rows_b)
    shared = [s for s in ida if s in idb]
    if len(shared) != len(ida) or len(shared) != len(idb):
        print(f"  (comparing {len(shared)} shared dossiers; A has {len(ida)}, B has {len(idb)})")
    rows_a, rows_b = [ida[s] for s in shared], [idb[s] for s in shared]

    ca, cb = rule_counts(rows_a), rule_counts(rows_b)
    known = [rid for rid, *_ in RULES]
    extra = sorted((set(ca) | set(cb)) - set(known))
    print("\n=== rule counts (dossiers with ≥1 violation): A → B ===")
    for rid in known + extra:
        if ca[rid] or cb[rid]:
            print(f"  {rid:<12}{fmt_delta(ca[rid], cb[rid])}")

    ha, hb = severity_hist(rows_a), severity_hist(rows_b)
    print("\n=== per-rule severity histogram (severity: violations) A → B ===")
    for rid in known + extra:
        if rid not in ha and rid not in hb:
            continue
        sa, sb = ha.get(rid, Counter()), hb.get(rid, Counter())
        if sa == sb:
            continue
        cells = [f"{sev}: {sa[sev]}→{sb[sev]}" for sev in sorted(set(sa) | set(sb), reverse=True)]
        print(f"  {rid:<5}" + "   ".join(cells))

    sa, sb = claim_split(rows_a), claim_split(rows_b)
    print("\n=== M6 claim status split A → B ===")
    for k in sorted(set(sa) | set(sb)):
        print(f"  {k:<14}{fmt_delta(sa[k], sb[k])}")

    print("\n=== deserved_attention flips ===")
    flips = [(s, ida[s], idb[s]) for s in shared
             if ida[s]["result"]["deserved_attention"] != idb[s]["result"]["deserved_attention"]]
    da = sum(1 for r in rows_a if r["result"]["deserved_attention"])
    db = sum(1 for r in rows_b if r["result"]["deserved_attention"])
    print(f"  total deserved {fmt_delta(da, db)};  {len(flips)} flip(s)")
    for s, a, b in flips:
        print(f"  {s}: {a['result']['deserved_attention']} → {b['result']['deserved_attention']}   "
              f"[{a['facts'].get('deserved_reason')}] → [{b['facts'].get('deserved_reason')}]")

    ta, tb = trigger_kinds(rows_a), trigger_kinds(rows_b)
    print("\n=== trigger kinds A → B ===")
    for k in sorted(set(ta) | set(tb)):
        print(f"  {k:<32}{fmt_delta(ta[k], tb[k])}")
    sa, sb = trigger_split(rows_a), trigger_split(rows_b)
    print("\n=== trigger provenance (dossiers) A → B ===")
    for k in sorted(set(sa) | set(sb)):
        print(f"  {k:<32}{fmt_delta(sa[k], sb[k])}")

    aa, ab = annotator_agreement(rows_a), annotator_agreement(rows_b)
    if aa:
        print("\n=== deserved_attention agreement with annotators A → B ===")
        for k in sorted(aa):
            (ga, na), (gb, nb) = aa[k], ab[k]
            print(f"  {k}: {ga}/{na} = {ga / max(1, na):.0%} → {gb}/{nb} = {gb / max(1, nb):.0%}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python analysis/compare_runs.py <run_a.jsonl> <run_b.jsonl>")
    main(sys.argv[1], sys.argv[2])
