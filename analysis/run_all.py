"""
ABOUTME: Runs SignalEvaluator over every dossier, saves the run under analysis/runs/ (see runlog), and prints
ABOUTME: the sanity tables: rule counts, sub-reasons, weekly M6 status, outcome lifts, annotator agreement.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval import RULES, SignalEvaluator  # noqa: E402
from signal_eval.labels import THRESHOLDS  # noqa: E402
from signal_eval.runlog import save_run  # noqa: E402

RUNS = Path(__file__).resolve().parent / "runs"
MANIFEST = RUNS / "manifest.jsonl"
CONTRACT_KEYS = ("quality_score", "risk_score", "deserved_attention", "violations")


def week(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%G-W%V")


def model_slug(model_id):
    """'MoritzLaurer/deberta-v3-base-zeroshot-v2.0' → 'deberta-v3-base-zeroshot-v2-0'; None → 'none'."""
    if not model_id:
        return "none"
    return re.sub(r"[^a-z0-9]+", "-", model_id.split("/")[-1].lower()).strip("-")


def main():
    D = E._load("signal_dossiers.jsonl")
    A = E._load("artifacts.jsonl")
    ev = SignalEvaluator(label_cache_path=E.LABEL_CACHE)      # None → labellers.default_cache_path(MODEL_ID)
    ev.load_context(E._load("accounts.jsonl"), E._load("owners.jsonl"), E._load("telemetry.jsonl"), A, D)
    arts = ev.cx.artifacts
    outcomes = {o["signal_id"]: o for o in E._load("outcomes.jsonl")}
    ann = {i: {r["signal_id"]: r for r in E._load(f"annotations/annotator_{i}.jsonl")} for i in (1, 2, 3)}

    results, rows = {}, []
    for d in D:
        r = ev.explain(d)
        rows.append({"signal_id": d["signal_id"], "result": {k: r[k] for k in CONTRACT_KEYS}, "facts": r["_facts"]})
        r["signal_id"], r["detector"], r["week"] = d["signal_id"], d["detector"], week(d["opened_at"])
        r["disposition"] = d["decision"]["disposition"]
        results[d["signal_id"]] = r
    model_id = getattr(ev.cx.labeller, "model_id", None) if ev.cx.classifier_active else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RUNS / f"{stamp}_{model_slug(model_id)}.jsonl"
    meta = save_run(out, rows, {"model_id": model_id, "classifier_reason": ev.cx.classifier_reason,
                                "thresholds": THRESHOLDS.get(model_id)})
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(dict(meta, file=out.name)) + "\n")
    print(f"wrote {len(rows)} rows → {out}  (manifest: {MANIFEST})\n")

    # 1. rule counts + sub-reason breakdown
    print("=== rule counts (dossiers with ≥1 violation of rule) ===")
    for rid, ref, sev, _ in RULES:
        n = sum(1 for r in results.values() if any(v["rule"] == rid for v in r["violations"]))
        print(f"{rid:<4}{n:>5}  [{sev}]")
    n_unev = sum(1 for r in results.values() if any(v["rule"] == "UNEVALUATED" for v in r["violations"]))
    print(f"UNEVALUATED {n_unev:>5}  [meta: text rules not evaluated — labeller {ev.cx.classifier_reason}]")
    print("\n=== sub-reasons for the big ones ===")
    for rid in ("I4", "T3", "M4", "M6", "I6", "P1", "I2", "Q2"):
        c = Counter()
        for r in results.values():
            for v in r["violations"]:
                if v["rule"] == rid:
                    key = v["explanation"].split(";")[0].split(":")[0][:60]
                    c[key] += 1
        print(f"\n{rid}:")
        for k, n in c.most_common(6):
            print(f"   {n:>4}  {k}")

    # 2. claim status per week
    print("\n=== M6 claim status by ISO week (top weeks) ===")
    wk = defaultdict(Counter)
    for r in results.values():
        for s in r["_facts"]["claim_status"]:
            wk[r["week"]][s] += 1
    for w in sorted(wk, key=lambda w: -sum(wk[w].values()))[:8]:
        print(f"  {w}: {dict(wk[w])}")

    # 2b. trigger split (confirmed / uncertain / historical / unattributed) and account-level misses
    print("\n=== mandatory-route triggers: confirmed / uncertain / historical / unattributed ===")
    src = Counter()
    for r in results.values():
        s = r["_facts"]["trigger_source"] or {}
        for kind in ("confirmed", "uncertain", "historical", "unattributed"):
            for t in s.get(kind, []):
                src[f"{kind}: {t}"] += 1
    for k, n in sorted(src.items()):
        print(f"  {n:>4}  {k}")
    p1 = [r for r in results.values() if any(v["rule"] == "P1" for v in r["violations"])]
    p1_conf = sum(1 for r in p1 if (r["_facts"]["trigger_source"] or {}).get("confirmed"))
    p1_unatt = sum(1 for r in p1 if not r["_facts"]["triggers"] and r["_facts"]["account_triggers_unattached"])
    print(f"  P1 dossiers: {len(p1)} = confirmed {p1_conf} + uncertain-only {len(p1) - p1_conf - p1_unatt} + unattached-only {p1_unatt}")
    trig = Counter(t for r in results.values() for t in r["_facts"]["triggers"])
    print("  final triggers (confirmed ∪ uncertain):", dict(trig))
    unatt = {s: r["_facts"]["account_triggers_unattached"] for s, r in results.items() if r["_facts"]["account_triggers_unattached"]}
    print(f"\n=== account carried a written trigger the agent never attached: {len(unatt)} dossiers "
          f"(labels cover corpus: {next(iter(results.values()))['_facts']['labels_cover_corpus']}) ===")
    by_disp = Counter(results[s]["disposition"] for s in unatt)
    print("  by disposition:", dict(by_disp))
    kinds = Counter(t for v in unatt.values() for _, _, ts_ in v for t in ts_)
    print("  by trigger kind:", dict(kinds))
    for label, ids in (("unattached-trigger dossiers", list(unatt)), ("all others", [s for s in results if s not in unatt])):
        xs = [outcomes[s] for s in ids if s in outcomes and outcomes[s]["renewal_outcome"] != "pending"]
        bad = sum(1 for o in xs if o["renewal_outcome"] in ("churned", "downgraded"))
        print(f"  {label}: churn/downgrade {bad}/{len(xs)} = {bad/max(1,len(xs)):.0%}")

    # 3. deserved rate
    print("\n=== deserved_attention ===")
    n_des = sum(r["deserved_attention"] for r in results.values())
    print(f"  overall {n_des}/{len(results)} = {n_des/len(results):.0%}")
    bydet = defaultdict(list)
    for r in results.values():
        bydet[r["detector"]].append(r["deserved_attention"])
    for k, v in sorted(bydet.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:<24}{sum(v):>4}/{len(v):<4} {sum(v)/len(v):.0%}")
    reasons = Counter(r["_facts"]["deserved_reason"] for r in results.values())
    for k, n in reasons.most_common():
        print(f"  {n:>4}  {k}")

    # 4. outcome lifts
    print("\n=== outcome rate with vs without each rule (complained / wasted / churn+downgrade) ===")
    def rate(ids, key):
        xs = [outcomes[s] for s in ids if s in outcomes]
        if not xs:
            return float("nan"), 0
        if key == "bad":
            hits = sum(1 for o in xs if o["renewal_outcome"] in ("churned", "downgraded"))
        else:
            hits = sum(1 for o in xs if o[key])
        return hits / len(xs), len(xs)
    all_ids = list(results)
    print(f"{'rule':<5}{'n':>5} {'complain':>9} {'wasted':>8} {'churn/dg':>9}   | without: complain wasted churn/dg")
    for rid, _, _, _ in RULES:
        with_ = [s for s in all_ids if any(v["rule"] == rid for v in results[s]["violations"])]
        without = [s for s in all_ids if s not in set(with_)]
        if not with_:
            continue
        cw, n = rate(with_, "customer_complained_about_outreach")
        ww, _ = rate(with_, "escalation_was_wasted")
        bw, _ = rate(with_, "bad")
        co, _ = rate(without, "customer_complained_about_outreach")
        wo, _ = rate(without, "escalation_was_wasted")
        bo, _ = rate(without, "bad")
        print(f"{rid:<5}{n:>5} {cw:>9.1%} {ww:>8.1%} {bw:>9.1%}   |          {co:>7.1%} {wo:>6.1%} {bo:>8.1%}")
    des = [s for s in all_ids if results[s]["deserved_attention"]]
    nd = [s for s in all_ids if not results[s]["deserved_attention"]]
    print(f"\n  deserved=True  n={len(des)} churn/dg {rate(des,'bad')[0]:.1%}  wasted {rate(des,'escalation_was_wasted')[0]:.1%}  complained {rate(des,'customer_complained_about_outreach')[0]:.1%}")
    print(f"  deserved=False n={len(nd)} churn/dg {rate(nd,'bad')[0]:.1%}  wasted {rate(nd,'escalation_was_wasted')[0]:.1%}  complained {rate(nd,'customer_complained_about_outreach')[0]:.1%}")

    # 5. annotator agreement
    print("\n=== agreement with annotators ===")
    for i in (1, 2, 3):
        ids = [s for s in ann[i] if s in results]
        agree = sum(1 for s in ids if ann[i][s]["deserved_attention"] == results[s]["deserved_attention"])
        ours = [results[s]["quality_score"] for s in ids]
        theirs = [ann[i][s]["quality_score"] for s in ids]
        # Spearman via ranks
        def ranks(xs):
            order = sorted(range(len(xs)), key=lambda k: xs[k])
            r = [0] * len(xs)
            for rank, k in enumerate(order):
                r[k] = rank
            return r
        ro, rt = ranks(ours), ranks(theirs)
        n = len(ids)
        mo, mt = sum(ro) / n, sum(rt) / n
        cov = sum((a - mo) * (b - mt) for a, b in zip(ro, rt))
        var = (sum((a - mo) ** 2 for a in ro) * sum((b - mt) ** 2 for b in rt)) ** 0.5
        rho = cov / var if var else 0
        ann_rate = sum(ann[i][s]["deserved_attention"] for s in ids) / n
        our_rate = sum(results[s]["deserved_attention"] for s in ids) / n
        print(f"  annotator_{i}: deserved agreement {agree}/{n} = {agree/n:.0%}  (they say yes {ann_rate:.0%}, we say yes {our_rate:.0%});  quality Spearman ρ = {rho:.2f}")

    # 6. worked examples
    print("\n=== worked examples ===")
    def show(sid):
        d = next(x for x in D if x["signal_id"] == sid)
        r = results[sid]
        o = outcomes.get(sid, {})
        print(f"\n{sid}  {d['account_id']}  {d['detector']}  disp={d['decision']['disposition']}  play={d['decision']['recommended_play']}")
        print(f"  quality={r['quality_score']}  risk={r['risk_score']}  deserved={r['deserved_attention']} ({r['_facts']['deserved_reason']})")
        print(f"  outcome={o.get('renewal_outcome')}  wasted={o.get('escalation_was_wasted')}  complained={o.get('customer_complained_about_outreach')}  useful={o.get('owner_marked_useful')}")
        for v in r["violations"]:
            print(f"    [{v['rule']} {v['severity']}] step {v['step']}: {v['explanation'][:140]}")
        for ev_ in d["evidence"]:
            a = arts.get(ev_["artifact_id"])
            if a and ev_["quote"] not in (a.get("subject") or "") + "\n" + a["text"]:
                print(f"    quote : {ev_['quote'][:100]}")
                print(f"    actual: {a['text'][:100]!r}")
    show("sig_0001")
    worst = sorted(results, key=lambda s: results[s]["quality_score"])[:4]
    for s in worst:
        show(s)


if __name__ == "__main__":
    main()
