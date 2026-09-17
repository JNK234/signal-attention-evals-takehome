"""
ABOUTME: Ledger for violations.md (README §2): per-rule counts, outcome lift with pending excluded, breakdowns by
ABOUTME: account tier / region / detector / owner, co-occurrence, and the harmless-vs-harmful test for common rules.
"""

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_eval.spec import RULES, RULE_SEVERITY  # noqa: E402

FACTS = ROOT / "analysis" / "facts.csv"
RUNS = ROOT / "analysis" / "runs"
BAD = {"churned", "downgraded"}
RULE_IDS = [r[0] for r in RULES]


def latest_run():
    runs = sorted(p for p in RUNS.glob("*.jsonl") if p.name != "manifest.jsonl")
    if not runs:
        sys.exit("no evaluator run found under analysis/runs/. Run `python analysis/run_all.py` first (about two minutes, "
                 "uses the committed label cache), then `python analysis/facts.py`.")
    with open(runs[-1]) as f:
        rows = [json.loads(l) for l in f]
    return runs[-1].name, rows[0]["_meta"]["git_sha"], {r["signal_id"]: r for r in rows[1:]}


def load_facts():
    if not FACTS.exists():
        sys.exit("analysis/facts.csv not found. Run `python analysis/run_all.py` then `python analysis/facts.py` first.")
    with open(FACTS) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_rules"] = {rid for rid in RULE_IDS if float(r.get(f"v_{rid}") or 0) > 0}
        r["_known"] = r["renewal_outcome"] not in ("", "pending", "None")
        r["_bad"] = r["renewal_outcome"] in BAD
        r["_wasted"] = r["wasted"] == "True"
        r["_complained"] = r["complained"] == "True"
        r["_routed"] = r["disposition"] in ("routed", "acknowledged")
        r["_critical"] = any(RULE_SEVERITY[x] == "critical" for x in r["_rules"])
    return rows


def rate(rows, pred, denom=None):
    d = [r for r in rows if denom(r)] if denom else rows
    return (sum(pred(r) for r in d) / len(d), len(d)) if d else (float("nan"), 0)


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (c - h, c + h)


def main():
    run_name, sha, run = latest_run()
    rows = load_facts()
    print(f"run {run_name} @ {sha}; dossiers {len(rows)}; known outcomes {sum(r['_known'] for r in rows)}")
    base_bad, nb = rate(rows, lambda r: r["_bad"], lambda r: r["_known"])
    base_w, _ = rate(rows, lambda r: r["_wasted"])
    base_c, _ = rate(rows, lambda r: r["_complained"])
    print(f"base rates: churn/downgrade {base_bad:.1%} (of {nb} known), wasted {base_w:.1%}, complained {base_c:.1%} (of 629)")

    # ── 1. per-rule counts and instances ──────────────────────────────────────
    inst = Counter()
    for r in run.values():
        inst.update(v["rule"] for v in r["result"]["violations"] if v["rule"] in RULE_SEVERITY)
    print("\n== 1. rules by frequency (dossiers with ≥1 finding; instances) ==")
    print(f"{'rule':<6}{'class':<10}{'dossiers':>9}{'share':>7}{'instances':>10}  spec reference")
    for rid, ref, sev, _ in sorted(RULES, key=lambda x: -sum(x[0] in r["_rules"] for r in rows)):
        n = sum(rid in r["_rules"] for r in rows)
        print(f"{rid:<6}{sev:<10}{n:>9}{n/len(rows):>7.0%}{inst[rid]:>10}  {ref}")
    print("\ndossiers with ≥1 critical finding:", sum(r["_critical"] for r in rows),
          " with any finding:", sum(bool(r["_rules"]) for r in rows),
          " clean:", sum(not r["_rules"] for r in rows))
    print("findings per dossier:", dict(sorted(Counter(min(len(r["_rules"]), 8) for r in rows).items())))

    # ── 2. outcome lift per rule, pending excluded, with the structural gate ──
    print("\n== 2. outcome rate with the rule vs without (churn/downgrade over known outcomes; wasted and complaint over all) ==")
    print(f"{'rule':<6}{'n':>4}{'routed':>7} | {'churn+':>7}{'churn-':>7}{'lift':>6}{'95%CI(with)':>16} | {'wasted+':>8}{'wasted-':>8}{'lift':>6} | {'cmpl+':>6}{'cmpl-':>6}{'lift':>6}  note")
    for rid, _, sev, _ in RULES:
        w = [r for r in rows if rid in r["_rules"]]
        wo = [r for r in rows if rid not in r["_rules"]]
        if not w:
            continue
        cb, ncb = rate(w, lambda r: r["_bad"], lambda r: r["_known"])
        cb0, _ = rate(wo, lambda r: r["_bad"], lambda r: r["_known"])
        wb, _ = rate(w, lambda r: r["_wasted"]); wb0, _ = rate(wo, lambda r: r["_wasted"])
        pb, _ = rate(w, lambda r: r["_complained"]); pb0, _ = rate(wo, lambda r: r["_complained"])
        routed = sum(r["_routed"] for r in w)
        lo, hi = wilson_ci(sum(r["_bad"] for r in w if r["_known"]), ncb)
        note = ""
        if routed == 0:
            note = "structural: fires only on unrouted dossiers, so wasted/complaint cannot occur"
        elif routed < len(w) * 0.25:
            note = "mostly unrouted"
        print(f"{rid:<6}{len(w):>4}{routed:>7} | {cb:>7.0%}{cb0:>7.0%}{cb/cb0 if cb0 else float('nan'):>6.2f}{f'[{lo:.0%},{hi:.0%}]':>16} | "
              f"{wb:>8.0%}{wb0:>8.0%}{wb/wb0 if wb0 else float('nan'):>6.2f} | {pb:>6.1%}{pb0:>6.1%}{pb/pb0 if pb0 else float('nan'):>6.2f}  {note}")

    # ── 3. breakdowns: share of dossiers with ≥1 critical finding, and mean findings, by group ─
    def breakdown(key, label):
        print(f"\n== 3. by {label} ==")
        print(f"{label:<24}{'n':>5}{'critical%':>10}{'findings/dossier':>17}{'routed%':>8}{'churn%':>7}{'wasted%':>8}  top three rules")
        groups = defaultdict(list)
        for r in rows:
            groups[r[key] or "-"].append(r)
        for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            top = Counter(x for r in rs for x in r["_rules"]).most_common(3)
            cb, _ = rate(rs, lambda r: r["_bad"], lambda r: r["_known"])
            print(f"{g:<24}{len(rs):>5}{sum(r['_critical'] for r in rs)/len(rs):>10.0%}"
                  f"{sum(len(r['_rules']) for r in rs)/len(rs):>17.2f}{sum(r['_routed'] for r in rs)/len(rs):>8.0%}"
                  f"{cb:>7.0%}{sum(r['_wasted'] for r in rs)/len(rs):>8.0%}  "
                  + ", ".join(f"{k} {v/len(rs):.0%}" for k, v in top))
    breakdown("tier", "account tier")
    breakdown("region", "region")
    breakdown("detector", "detector")
    breakdown("owner_id", "owner")

    # ── 4. per-rule rate by detector (the rules that vary most across detectors) ─
    print("\n== 4. rule rate by detector (share of the detector's dossiers) ==")
    dets = [d for d, _ in Counter(r["detector"] for r in rows).most_common()]
    print(f"{'rule':<6}" + "".join(f"{d[:12]:>13}" for d in dets))
    for rid in RULE_IDS:
        per = []
        for d in dets:
            rs = [r for r in rows if r["detector"] == d]
            per.append(sum(rid in r["_rules"] for r in rs) / len(rs))
        if max(per) - min(per) >= 0.15:
            print(f"{rid:<6}" + "".join(f"{p:>13.0%}" for p in per))

    # ── 5. co-occurrence: pairs that fire together far more than independence predicts ─
    print("\n== 5. co-occurrence (observed / expected under independence, pairs with ≥10 observed) ==")
    n = len(rows)
    pairs = []
    for i, a in enumerate(RULE_IDS):
        na = sum(a in r["_rules"] for r in rows)
        for b in RULE_IDS[i + 1:]:
            nb_ = sum(b in r["_rules"] for r in rows)
            ob = sum(a in r["_rules"] and b in r["_rules"] for r in rows)
            if ob >= 10 and na and nb_:
                pairs.append((ob / (na * nb_ / n), a, b, ob))
    for ratio, a, b, ob in sorted(pairs, reverse=True)[:10]:
        print(f"  {a:<6}{b:<6} observed {ob:>4}  ratio {ratio:.1f}×")

    # ── 6. common-but-harmless test: for the most frequent rules, does firing change any outcome among ROUTED dossiers? ─
    print("\n== 6. common rules: outcome among routed dossiers, with vs without (this is the harmless test) ==")
    print(f"{'rule':<6}{'routed with':>12}{'churn':>7}{'wasted':>8}{'useful':>8} | {'routed without':>15}{'churn':>7}{'wasted':>8}{'useful':>8}")
    for rid in ("Q2", "M6", "§6.3", "§5", "§4.8", "§8.5", "M4", "I6", "Q4"):
        w = [r for r in rows if r["_routed"] and rid in r["_rules"]]
        wo = [r for r in rows if r["_routed"] and rid not in r["_rules"]]
        def u(rs):
            k = [r for r in rs if r["owner_marked_useful"] in ("True", "False")]
            return sum(r["owner_marked_useful"] == "True" for r in k) / len(k) if k else float("nan")
        cb, _ = rate(w, lambda r: r["_bad"], lambda r: r["_known"]); cb0, _ = rate(wo, lambda r: r["_bad"], lambda r: r["_known"])
        print(f"{rid:<6}{len(w):>12}{cb:>7.0%}{sum(r['_wasted'] for r in w)/len(w):>8.0%}{u(w):>8.0%} | "
              f"{len(wo):>15}{cb0:>7.0%}{sum(r['_wasted'] for r in wo)/len(wo):>8.0%}{u(wo):>8.0%}")

    # ── 6b. the same test inside one detector, so the detector's own base rate cannot leak in ─
    print("\n== 6b. harmless test within detector: routed dossiers of usage_cliff and seat_decay only ==")
    print(f"{'rule':<6}{'detector':<14}{'with n':>7}{'churn':>7}{'wasted':>8} | {'without n':>10}{'churn':>7}{'wasted':>8}")
    for rid in ("M6", "Q2", "§6.3", "M4"):
        for det in ("usage_cliff", "seat_decay"):
            w = [r for r in rows if r["_routed"] and r["detector"] == det and rid in r["_rules"]]
            wo = [r for r in rows if r["_routed"] and r["detector"] == det and rid not in r["_rules"]]
            cb, _ = rate(w, lambda r: r["_bad"], lambda r: r["_known"]); cb0, _ = rate(wo, lambda r: r["_bad"], lambda r: r["_known"])
            ww = sum(r["_wasted"] for r in w) / len(w) if w else float("nan"); ww0 = sum(r["_wasted"] for r in wo) / len(wo) if wo else float("nan")
            print(f"{rid:<6}{det:<14}{len(w):>7}{cb:>7.0%}{ww:>8.0%} | {len(wo):>10}{cb0:>7.0%}{ww0:>8.0%}")
    # complaints: how many of the 16 carry each rule
    print("\n== 6c. the 16 complaints: which rules they carry ==")
    cm = [r for r in rows if r["_complained"]]
    print("  ", {rid: sum(rid in r["_rules"] for r in cm) for rid in ("§8.2", "§6.3", "M4", "M6", "I6", "§8.5", "Q2")}, " customer-visible play:", sum(r["customer_visible"] == "True" for r in cm))

    # ── 6d. the outcome comparisons on their correct denominators ─
    print("\n== 6d. wasted-escalation rate among ROUTED dossiers only (the field exists only there) ==")
    routed = [r for r in rows if r["_routed"]]
    print(f"  routed {len(routed)}, wasted {sum(r['_wasted'] for r in routed)} = {sum(r['_wasted'] for r in routed)/len(routed):.0%}")
    for rid in ("§6.1", "M4", "§8.6", "§8.3", "§8.2", "§6.3", "I6", "§8.5", "M6", "Q2"):
        w = [r for r in routed if rid in r["_rules"]]; wo = [r for r in routed if rid not in r["_rules"]]
        if w:
            print(f"  {rid:<6} with n={len(w):>3} wasted {sum(r['_wasted'] for r in w)/len(w):>4.0%} | without n={len(wo):>3} wasted {sum(r['_wasted'] for r in wo)/len(wo):>4.0%}")
    print("\n== 6e. complaint rate among ROUTED, CUSTOMER-VISIBLE dossiers only (the only place a complaint can occur) ==")
    cv = [r for r in routed if r["customer_visible"] == "True"]
    print(f"  routed customer-visible {len(cv)}, complaints {sum(r['_complained'] for r in cv)}")
    for rid in ("§8.2", "§6.3", "M4", "M6", "I6", "Q2"):
        w = [r for r in cv if rid in r["_rules"]]; wo = [r for r in cv if rid not in r["_rules"]]
        print(f"  {rid:<6} with n={len(w):>3} complaints {sum(r['_complained'] for r in w):>2} = {sum(r['_complained'] for r in w)/len(w):>4.0%} | without n={len(wo):>3} {sum(r['_complained'] for r in wo):>2} = {sum(r['_complained'] for r in wo)/len(wo):>4.0%}")
    print("\n== 6f. M6 split by the evaluator's own reading of the claim (routed dossiers, churn/downgrade) ==")
    kinds = defaultdict(list)
    for r in rows:
        if "M6" not in r["_rules"]:
            continue
        ex = [v["explanation"] for v in run[r["signal_id"]]["result"]["violations"] if v["rule"] == "M6"]
        k = "inflated real decline" if any("inflated" in e for e in ex) else "manufactured decline" if any("manufactured" in e for e in ex) else "wrong on both series"
        kinds[k].append(r)
    for k, rs in kinds.items():
        rr = [r for r in rs if r["_routed"]]
        cb, n = rate(rr, lambda r: r["_bad"], lambda r: r["_known"])
        print(f"  {k:<24} dossiers {len(rs):>3}, routed {len(rr):>3}, churn/dg among routed with known outcome {cb:.0%} (n={n})")
    print("\n== 6g. §6.3 late notification: churn within severity (routed dossiers) ==")
    for sev in ("P1", "P2"):
        w = [r for r in routed if r["severity"] == sev and "§6.3" in r["_rules"]]; wo = [r for r in routed if r["severity"] == sev and "§6.3" not in r["_rules"]]
        cb, n1 = rate(w, lambda r: r["_bad"], lambda r: r["_known"]); cb0, n0 = rate(wo, lambda r: r["_bad"], lambda r: r["_known"])
        print(f"  {sev}: late {cb:.0%} (n={n1}) vs on time {cb0:.0%} (n={n0})")
    print("  §6.3 dossiers by severity:", dict(Counter(r["severity"] for r in rows if "§6.3" in r["_rules"])))
    print("\n== 6h. owner spread: chi-square across the 14 CSMs on critical share; restricted-account share vs §8.2 ==")
    csm = defaultdict(list)
    for r in rows:
        if r["owner_id"].startswith("u_0"):
            csm[r["owner_id"]].append(r)
    tot = sum(len(v) for v in csm.values()); crit = sum(r["_critical"] for v in csm.values() for r in v)
    chi = sum((sum(r["_critical"] for r in v) - len(v) * crit / tot) ** 2 / (len(v) * crit / tot) +
              ((len(v) - sum(r["_critical"] for r in v)) - len(v) * (1 - crit / tot)) ** 2 / (len(v) * (1 - crit / tot)) for v in csm.values())
    print(f"  chi-square {chi:.1f} on {len(csm)-1} df (critical value at p=0.05 is 22.4)")
    for o, v in sorted(csm.items(), key=lambda kv: -sum("§8.2" in r["_rules"] for r in kv[1]))[:6]:
        flagged = sum(r["restricted_account"] == "True" for r in v) / len(v)
        print(f"  {o}: restricted accounts {flagged:.0%}, §8.2 findings {sum('§8.2' in r['_rules'] for r in v)}")
    print("\n== 6i. Q2 validity checks ==")
    def ann_flag(r, cat):
        return any(cat in (r.get(f"ann{i}_cats") or "") for i in (1, 2, 3))
    def ann_n(r):
        return sum(1 for i in (1, 2, 3) if r.get(f"ann{i}_quality"))
    q2 = [r for r in rows if "Q2" in r["_rules"] and ann_n(r)]; nq2 = [r for r in rows if "Q2" not in r["_rules"] and ann_n(r)]
    print(f"  annotator wrong_hypothesis flag: Q2 dossiers {sum(ann_flag(r,'wrong_hypothesis') for r in q2)}/{len(q2)} = {sum(ann_flag(r,'wrong_hypothesis') for r in q2)/len(q2):.0%}; "
          f"non-Q2 {sum(ann_flag(r,'wrong_hypothesis') for r in nq2)}/{len(nq2)} = {sum(ann_flag(r,'wrong_hypothesis') for r in nq2)/len(nq2):.0%}")
    sup = [r for r in rows if not r["_routed"]]
    cb, n1 = rate([r for r in sup if "Q2" in r["_rules"]], lambda r: r["_bad"], lambda r: r["_known"]); cb0, n0 = rate([r for r in sup if "Q2" not in r["_rules"]], lambda r: r["_bad"], lambda r: r["_known"])
    print(f"  unrouted dossiers: churn with Q2 {cb:.0%} (n={n1}) vs without {cb0:.0%} (n={n0})")
    import statistics
    def annq(rs):
        v = [float(r[f"ann{i}_quality"]) for r in rs for i in (1, 2, 3) if r.get(f"ann{i}_quality")]
        return statistics.median(v) if v else float("nan"), len(v)
    print(f"  annotator quality median: M1∪M5 dossiers {annq([r for r in rows if r['_rules'] & {'M1','M5'}])}; rest {annq([r for r in rows if not (r['_rules'] & {'M1','M5'})])}")
    print("\n== 6j. I6 ∩ §8.7: is the appended email in the artefact? ==")
    arts = {a["artifact_id"]: a for a in map(json.loads, open(ROOT / "data" / "artifacts.jsonl"))}
    import re
    both = [r for r in rows if {"I6", "§8.7"} <= r["_rules"]]
    inart = 0
    for r in both:
        d = next(v for v in run[r["signal_id"]]["result"]["violations"] if v["rule"] == "§8.7")
        aid = re.search(r"art_\d+", d["explanation"]); 
        quote = next((v["explanation"] for v in run[r["signal_id"]]["result"]["violations"] if v["rule"] == "I6"), "")
        em = re.search(r"[\w.]+@[\w.]+", quote)
        if aid and em and em.group(0) in (arts.get(aid.group(0), {}).get("text") or ""):
            inart += 1
    print(f"  {len(both)} dossiers; appended address present in the artefact text: {inart}")

    # ── 7. quality score vs arr_at_risk trustworthiness ─
    print("\n== 7. quality_score by whether arr_at_risk is provably wrong (M1 or M5) ==")
    import statistics
    wrong = [float(run[r["signal_id"]]["result"]["quality_score"]) for r in rows if r["_rules"] & {"M1", "M5"}]
    ok = [float(run[r["signal_id"]]["result"]["quality_score"]) for r in rows if not (r["_rules"] & {"M1", "M5"})]
    print(f"  wrong n={len(wrong)} median quality {statistics.median(wrong):.3f};  not wrong n={len(ok)} median {statistics.median(ok):.3f}")

    # ── 8. example dossiers per headline rule: pick the routed one with a known bad outcome where possible ─
    print("\n== 8. example dossiers (rule → signal, disposition, outcome, first explanation) ==")
    for rid in ("§8.2", "§8.1", "I6", "§8.7", "§8.3", "§8.4", "M6", "M4", "M5", "§6.3", "§6.1", "§5", "Q2"):
        cands = [r for r in rows if rid in r["_rules"]]
        cands.sort(key=lambda r: (not r["_routed"], not (r["_complained"] or r["_wasted"] or r["_bad"])))
        for r in cands[:2]:
            v = next(v for v in run[r["signal_id"]]["result"]["violations"] if v["rule"] == rid)
            print(f"  {rid:<6}{r['signal_id']} {r['account_id']} {r['detector']:<20} {r['disposition']:<12} {r['renewal_outcome']:<17} "
                  f"cmpl={r['_complained']!s:<5} wasted={r['_wasted']!s:<5} step {v['step']}: {v['explanation'][:110]}")


if __name__ == "__main__":
    main()
