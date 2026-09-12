"""
ABOUTME: Answers spec §3.1's empirical question per detector: precision (what fired was real?) and
ABOUTME: coverage (did it fire when the corrected data / the text says it should have?).
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval import SignalEvaluator  # noqa: E402
from signal_eval.util import day, mean, ts  # noqa: E402

# spec §3.1 detector definitions (week-over-week drop thresholds)
TELEMETRY_DETECTORS = {"usage_cliff": ("api_calls", 34.0), "seat_decay": ("dau_seats", 29.0)}
WINDOW = 7
MATCH_DAYS = 3          # a dossier opened within this many days of a qualifying day counts as "fired for it"
TEXT_WINDOW_DAYS = 14   # a written trigger with no dossier on the account within this window is a miss


def wow(rows, end, metric, cx):
    after, a_miss, a_bad, _ = cx.window(rows, end, WINDOW)
    before, b_miss, b_bad, _ = cx.window(rows, end - timedelta(days=WINDOW), WINDOW)
    if a_bad + b_bad or len(after) < WINDOW // 2 or len(before) < WINDOW // 2:
        return None, "contaminated"
    b, a = mean([r[metric] for r in before]), mean([r[metric] for r in after])
    if not b:
        return None, "zero baseline"
    return (a - b) / b * 100, "ok"


def raw_wow(rows_raw, end, metric):
    """What a naive detector sees: raw rows, missing days contribute nothing."""
    a = sum(rows_raw.get(end - timedelta(days=i), {}).get(metric, 0) for i in range(WINDOW))
    b = sum(rows_raw.get(end - timedelta(days=WINDOW + i), {}).get(metric, 0) for i in range(WINDOW))
    return (a - b) / b * 100 if b else None


def main():
    D = E._load("signal_dossiers.jsonl")
    tel_raw = E._load("telemetry.jsonl")
    ev = SignalEvaluator(label_cache_path=E.LABEL_CACHE if E.LABEL_CACHE.exists() else None, label_scope="all")
    ev.load_context(E._load("accounts.jsonl"), E._load("owners.jsonl"), tel_raw, E._load("artifacts.jsonl"), D)
    cx = ev.cx
    outcomes = {o["signal_id"]: o for o in E._load("outcomes.jsonl")}
    results = {d["signal_id"]: ev.evaluate(d) for d in D}

    # raw (uncorrected, deduped-by-latest) rows for the naive detector view
    raw = defaultdict(dict)
    latest = {}
    for r in tel_raw:
        k = (r["account_id"], r["date"])
        if k not in latest or r["ingested_at"] > latest[k]["ingested_at"]:
            latest[k] = r
    for (acct, ds), r in latest.items():
        raw[acct][day(ds)] = r

    fired = defaultdict(list)   # detector -> [(account, opened_date, signal_id)]
    for d in D:
        fired[d["detector"]].append((d["account_id"], day(d["opened_at"]), d["signal_id"]))

    print("=== PRECISION: what the fired signals were about ===")
    bad = lambda s: outcomes[s]["renewal_outcome"] in ("churned", "downgraded")
    settled = lambda s: outcomes[s]["renewal_outcome"] != "pending"
    for det in sorted(fired, key=lambda k: -len(fired[k])):
        ids = [s for _, _, s in fired[det]]
        st = Counter()
        for s in ids:
            f = results[s]["_facts"]
            if det in TELEMETRY_DETECTORS:
                st[f["claim_status"][0] if f["claim_status"] else "no claim"] += 1
            elif det == "exec_churn_language":
                trig = set(f["triggers"])
                st["real trigger" if trig & {"cancel_intent", "legal_reference", "security_incident", "buyer_or_champion_departure"} else
                   ("stale only" if f["n_stale_evidence"] and not f["has_customer_text"] else "no trigger found")] += 1
            elif det == "billing_dispute":
                st["≥5% ARR" if "billing_dispute" in f["triggers"] else "below 5% / unverified"] += 1
            else:
                st["deserved" if results[s]["deserved_attention"] else "not deserved"] += 1
        sett = [s for s in ids if settled(s)]
        churn = sum(map(bad, sett)) / max(1, len(sett))
        print(f"\n{det}: n={len(ids)}  churn/downgrade {churn:.0%} (n={len(sett)})")
        for k, n in st.most_common():
            print(f"   {n:>4}  {k}")

    print("\n=== COVERAGE: telemetry detectors re-run on corrected data ===")
    all_days = sorted({d for rows in cx.tel.values() for d in rows})
    for det, (metric, thr) in TELEMETRY_DETECTORS.items():
        real_events, naive_events = set(), set()
        for acct, rows in cx.tel.items():
            rr = raw.get(acct, {})
            for end in all_days:
                pct, why = wow(rows, end, metric, cx)
                if pct is not None and pct <= -thr:
                    real_events.add((acct, end))
                npct = raw_wow(rr, end, metric)
                if npct is not None and npct <= -thr:
                    naive_events.add((acct, end))
        # collapse runs: a decline crossing the threshold on consecutive days is one event
        def episodes(events):
            eps = defaultdict(list)
            for acct, end in sorted(events):
                if eps[acct] and (end - eps[acct][-1][-1]).days <= 1:
                    eps[acct][-1].append(end)
                else:
                    eps[acct].append([end])
            return [(a, e[0], e[-1]) for a, es in eps.items() for e in es]
        real_eps, naive_eps = episodes(real_events), episodes(naive_events)
        fires = fired[det]

        def matched(eps):
            hit = 0
            for acct, lo, hi in eps:
                if any(a == acct and lo - timedelta(days=MATCH_DAYS) <= od <= hi + timedelta(days=MATCH_DAYS) for a, od, _ in fires):
                    hit += 1
            return hit
        fire_real = sum(1 for a, od, _ in fires if any(a == acct and lo - timedelta(days=MATCH_DAYS) <= od <= hi + timedelta(days=MATCH_DAYS) for acct, lo, hi in real_eps))
        print(f"\n{det} (> {thr:.0f}% WoW drop in {metric}):")
        print(f"   real episodes on corrected telemetry : {len(real_eps)}   — detector fired for {matched(real_eps)}  → coverage {matched(real_eps)/max(1,len(real_eps)):.0%}")
        print(f"   episodes a naive (raw) detector sees  : {len(naive_eps)}")
        print(f"   dossiers fired                        : {len(fires)}   — matching a real episode {fire_real}  → precision {fire_real/max(1,len(fires)):.0%}")
        missed = [(a, lo) for a, lo, hi in real_eps if not any(a == acct and lo - timedelta(days=MATCH_DAYS) <= od <= hi + timedelta(days=MATCH_DAYS) for acct, od, _ in fires)]
        if missed:
            acct_ids = {a for a, _ in missed}
            churned = sum(1 for o in outcomes.values() if o["account_id"] in acct_ids and o["renewal_outcome"] in ("churned", "downgraded"))
            print(f"   missed real episodes: {len(missed)} on {len(acct_ids)} accounts; sample {missed[:4]}")

    print("\n=== COVERAGE: written triggers on accounts with no signal nearby ===")
    if not cx.account_triggers:
        print("   (no corpus-wide labels loaded — run analysis/label_all_artifacts.py first)")
        return
    opened_by_acct = defaultdict(list)
    for d in D:
        opened_by_acct[d["account_id"]].append((ts(d["opened_at"]), d["detector"], d["signal_id"]))
    miss = Counter()
    miss_ex = []
    total = Counter()
    for acct, items in cx.account_triggers.items():
        for when, aid, kinds in items:
            for k in kinds:
                total[k] += 1
                near = [s for (t, det, s) in opened_by_acct.get(acct, []) if abs((t - when).days) <= TEXT_WINDOW_DAYS]
                if not near:
                    miss[k] += 1
                    if len(miss_ex) < 6:
                        miss_ex.append((acct, aid, k, when.date().isoformat()))
    for k in total:
        print(f"   {k:<30} written {total[k]:>3}   no dossier within ±{TEXT_WINDOW_DAYS}d: {miss[k]:>3}  → coverage {(total[k]-miss[k])/total[k]:.0%}")
    print("   sample misses:", miss_ex)


if __name__ == "__main__":
    main()
