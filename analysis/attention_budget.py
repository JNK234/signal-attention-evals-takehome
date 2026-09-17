"""
ABOUTME: Attention-budget analysis (README §3): ranks every dossier, applies the 5-per-owner-week cap with a
ABOUTME: floor, and scores the policy against the agent's actual routing on outcome and annotator checks.
"""

import csv
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RUNS = ROOT / "analysis" / "runs"
BUDGET = 5                      # README: ~5 investigated signals per CSM per week
BURST_MIN = 40                  # a burst week holds at least this many signals, about twice the median of 21
SEV_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
BAD = {"churned", "downgraded"}


def jl(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def latest_run():
    runs = sorted(p for p in RUNS.glob("*.jsonl") if p.name != "manifest.jsonl")
    rows = jl(runs[-1])
    return runs[-1].name, {r["signal_id"]: r for r in rows if "signal_id" in r}


def iso_week(d):
    y, w, _ = date.fromisoformat(d["opened_at"][:10]).isocalendar()
    return f"{y}-W{w:02d}"


# ── per-dossier record ───────────────────────────────────────────────────────
def build(dossiers, run, outcomes, majority):
    recs = []
    for d in dossiers:
        r = run[d["signal_id"]]
        res, fx, md, sc = r["result"], r["facts"], d["metadata"], d.get("scoring") or {}
        cs = fx.get("claim_status") or []
        hyp = (d.get("hypotheses") or [{}])[0].get("hypothesis")
        deserved = bool(res["deserved_attention"])
        grounded_real = ("grounded" in cs) and hyp not in ("benign_variation", "no_hypothesis") and not fx.get("cohort_match")
        artefact_only = ("artifact" in cs and "grounded" not in cs) or bool(fx.get("cohort_match"))
        tier = 1 if deserved else 2 if grounded_real else 4 if artefact_only else 3
        o = outcomes.get(d["signal_id"], {})
        recs.append({
            "signal_id": d["signal_id"], "account_id": d["account_id"], "owner_id": md["owner_id"],
            "week": iso_week(d), "detector": d["detector"], "tier": tier, "deserved": deserved,
            "deserved_reason": fx.get("deserved_reason"), "severity": sc.get("severity"),
            "sev_rank": SEV_RANK.get(sc.get("severity"), 9), "confidence": sc.get("confidence"),
            "days_to_renewal": fx.get("days_to_renewal"), "arr_annual": md["arr_annual"] or 0,
            "arr_at_risk": sc.get("arr_at_risk") or 0, "risk": res["risk_score"], "quality": res["quality_score"],
            "agent_routed": (d.get("decision") or {}).get("disposition") in ("routed", "acknowledged"),
            "customer_visible": bool(fx.get("customer_visible")),
            "outcome": o.get("renewal_outcome"), "arr_delta": o.get("arr_delta") or 0,
            "attribution": o.get("attribution"), "concurrent": o.get("concurrent_interventions") or [],
            "complained": bool(o.get("customer_complained_about_outreach")),
            "wasted": bool(o.get("escalation_was_wasted")),
            "annot": majority.get(d["signal_id"]),
        })
    return recs


# ── ranking keys (lower sorts first) ─────────────────────────────────────────
def dtr(r):
    """Days to renewal as an urgency key: soonest first; a renewal already passed or unknown is not urgent."""
    d = r["days_to_renewal"]
    return d if d is not None and d >= 0 else 10**6


KEYS = {
    # proposed: tier (spec obligation → real grounded decline → rest → pipeline artefact), then the agent's own
    # severity, then renewal proximity, then contract value from accounts.jsonl (never the agent's arr_at_risk)
    "tiered":        lambda r: (r["tier"], r["sev_rank"], dtr(r), -r["arr_annual"]),
    # option (a) from the working note: deserved first, risk_score tiebreak
    "deserved+risk": lambda r: (not r["deserved"], -r["risk"], dtr(r)),
    # option (b): expected loss with the independent ARR field
    "risk×arr":      lambda r: (-(r["risk"] * r["arr_annual"]),),
    # what the agent's own scoring implies: severity, confidence, its own ARR estimate
    "agent-score":   lambda r: (r["sev_rank"], {"high": 0, "medium": 1, "low": 2}.get(r["confidence"], 3), -r["arr_at_risk"]),
}
FLOOR = {"tiered": lambda r: r["tier"] <= 2}      # only the proposed key leaves slots empty below a line


def apply_policy(recs, key, floor=None, budget=BUDGET):
    """Rank within each owner-week; route the top `budget` that clear the floor. Returns rank map + routed set."""
    by = defaultdict(list)
    for r in recs:
        by[(r["owner_id"], r["week"])].append(r)
    rank, routed = {}, set()
    for ow, rs in by.items():
        rs.sort(key=key)
        slots = 0
        for i, r in enumerate(rs, 1):
            rank[r["signal_id"]] = i
            if slots < budget and (floor is None or floor(r)):
                routed.add(r["signal_id"])
                slots += 1
    return rank, routed


def worst_loss(recs):
    """Lost ARR per account. outcomes.jsonl is a per-signal file and contradicts itself inside an account (92 of
    176 accounts carry two or more different renewal outcomes for one renewal date), so the account's loss is
    taken as the worst outcome any of its signals recorded, and counted once."""
    worst = {}
    for r in recs:
        if r["outcome"] in BAD:
            worst[r["account_id"]] = min(worst.get(r["account_id"], 0), r["arr_delta"])
    return worst


# ── scoring ──────────────────────────────────────────────────────────────────
def pr(routed, positives, universe):
    tp = len(routed & positives & universe)
    p = tp / len(routed & universe) if routed & universe else float("nan")
    rc = tp / len(positives & universe) if positives & universe else float("nan")
    return tp, p, rc


def summarize(name, recs, routed):
    R = [r for r in recs if r["signal_id"] in routed]
    known = [r for r in R if r["outcome"] and r["outcome"] != "pending"]
    bad = [r for r in known if r["outcome"] in BAD]
    # ARR is lost once per account, however many signals the account produced: count each account once, and
    # credit a policy with the account if it routed at least one of that account's signals.
    lost_by_acct = worst_loss(recs)
    lost_all = sum(lost_by_acct.values())
    reached = {r["account_id"] for r in R}
    lost_in = sum(v for a, v in lost_by_acct.items() if a in reached)
    des = {r["signal_id"] for r in recs if r["deserved"]}
    ann_pos = {r["signal_id"] for r in recs if r["annot"] is True}
    ann_uni = {r["signal_id"] for r in recs if r["annot"] is not None}
    tp_d, p_d, r_d = pr(routed, des, {r["signal_id"] for r in recs})
    tp_a, p_a, r_a = pr(routed, ann_pos, ann_uni)
    return {
        "policy": name, "routed": len(R),
        "prec_vs_deserved": p_d, "rec_vs_deserved": r_d,
        "prec_vs_annot": p_a, "rec_vs_annot": r_a, "annot_n": len(routed & ann_uni),
        "churn_rate": len(bad) / len(known) if known else float("nan"), "churn_n": f"{len(bad)}/{len(known)}",
        "lost_arr_captured": lost_in, "lost_arr_share": lost_in / lost_all if lost_all else float("nan"),
        "complaints": sum(r["complained"] for r in R), "wasted": sum(r["wasted"] for r in R),
        "cust_visible": sum(r["customer_visible"] for r in R),
    }


def fmt(row):
    return (f"{row['policy']:<16}{row['routed']:>7}  "
            f"{row['prec_vs_deserved']:>6.2f} {row['rec_vs_deserved']:>6.2f}   "
            f"{row['prec_vs_annot']:>6.2f} {row['rec_vs_annot']:>6.2f} (n={row['annot_n']:>3})   "
            f"{row['churn_rate']:>5.0%} {row['churn_n']:>8}   ${row['lost_arr_captured']/-1e6:>5.2f}M {row['lost_arr_share']:>4.0%}   "
            f"{row['complaints']:>4} {row['wasted']:>6} {row['cust_visible']:>6}")


HEADER = (f"{'policy':<16}{'routed':>7}  {'P|des':>6} {'R|des':>6}   {'P|ann':>6} {'R|ann':>6}         "
          f"{'churn':>5} {'(n)':>8}   {'lostARR':>7} {'share':>4}   {'cmpl':>4} {'wasted':>6} {'visib':>6}")


def main():
    run_name, run = latest_run()
    dossiers = jl(DATA / "signal_dossiers.jsonl")
    outcomes = {o["signal_id"]: o for o in jl(DATA / "outcomes.jsonl")}
    owners = {o["owner_id"]: o for o in jl(DATA / "owners.jsonl")}
    votes = defaultdict(dict)
    for i in (1, 2, 3):
        for a in jl(DATA / "annotations" / f"annotator_{i}.jsonl"):
            votes[a["signal_id"]][i] = bool(a["deserved_attention"])
    majority = {s: sum(v.values()) * 2 > len(v) for s, v in votes.items() if len(v) == 3}
    recs = build(dossiers, run, outcomes, majority)
    print(f"run: {run_name}   dossiers: {len(recs)}   three-way annotated: {len(majority)}")

    # ── setup facts ───────────────────────────────────────────────────────────
    by = defaultdict(list)
    for r in recs:
        by[(r["owner_id"], r["week"])].append(r)
    over = {k: v for k, v in by.items() if len(v) > BUDGET}
    print(f"\nowner-weeks with ≥1 candidate: {len(by)}   with >{BUDGET}: {len(over)} "
          f"(candidates in them: {sum(len(v) for v in over.values())}; weeks: {dict(Counter(k[1] for k in over))})")
    print("tiers:", dict(sorted(Counter(r['tier'] for r in recs).items())),
          "  max deserved in one owner-week:", max(sum(r['deserved'] for r in v) for v in by.values()),
          "  max tier≤2 in one owner-week:", max(sum(r['tier'] <= 2 for r in v) for v in by.values()))
    agent = {r["signal_id"] for r in recs if r["agent_routed"]}
    print(f"agent routed {len(agent)}; owner-weeks where agent exceeded {BUDGET}: "
          f"{sum(1 for v in by.values() if sum(r['agent_routed'] for r in v) > BUDGET)}")

    # ── Q1: the burst weeks ───────────────────────────────────────────────────
    accounts = {a["account_id"]: a for a in jl(DATA / "accounts.jsonl")}
    byweek = defaultdict(list)
    for d in dossiers:
        byweek[iso_week(d)].append(d)
    counts = sorted(len(v) for v in byweek.values())
    med = statistics.median(counts)
    print(f"\n== Q1: weeks {len(byweek)}, median {med:.0f}, max {max(counts)}; top-4 share "
          f"{sum(counts[-4:]) / len(dossiers):.0%}; opened on a Sunday {sum(date.fromisoformat(d['opened_at'][:10]).weekday() == 6 for d in dossiers)}/{len(dossiers)} ==")
    print(f"{'week':<9}{'n':>4}{'sunday':>7}  {'top detector':<22}{'top metric':<12}{'legacy':>7}{'apac+emea':>10}{'real':>6}{'artefact':>9}{'wrong':>6}{'benign':>7}{'routed':>7}")
    for wk in sorted(byweek):
        ds = byweek[wk]
        if len(ds) < BURST_MIN:      # the README's bursts: about twice the median week
            continue
        cs = Counter(st for d in ds for st in (run[d["signal_id"]]["facts"].get("claim_status") or []))
        print(f"{wk:<9}{len(ds):>4}{sum(date.fromisoformat(d['opened_at'][:10]).weekday() == 6 for d in ds):>7}  "
              f"{Counter(d['detector'] for d in ds).most_common(1)[0][0]:<22}"
              f"{Counter(m.get('metric') for d in ds for m in d.get('metrics_claimed') or []).most_common(1)[0][0] or '-':<12}"
              f"{sum(accounts[d['account_id']].get('collector') == 'legacy' for d in ds):>7}"
              f"{sum(accounts[d['account_id']].get('region') in ('apac', 'emea') for d in ds):>10}"
              f"{cs.get('grounded', 0):>6}{cs.get('artifact', 0):>9}{cs.get('wrong', 0):>6}"
              f"{sum((d.get('hypotheses') or [{}])[0].get('hypothesis') == 'benign_variation' for d in ds):>7}"
              f"{sum((d.get('decision') or {}).get('disposition') in ('routed', 'acknowledged') for d in ds):>7}")

    # ── tier evidence ─────────────────────────────────────────────────────────
    print("\n== tiers against outcomes and annotators ==")
    for t in (1, 2, 3, 4):
        s = summarize(f"tier {t}", recs, {r["signal_id"] for r in recs if r["tier"] == t})
        print(f"  tier {t}: n={s['routed']:3}  churn/dg {s['churn_rate']:.0%} ({s['churn_n']})  lost ARR ${s['lost_arr_captured']/-1e6:.2f}M  "
              f"annotator-majority deserved {s['prec_vs_annot']:.0%} (n={s['annot_n']})  agent routed {sum(r['agent_routed'] for r in recs if r['tier']==t)}")

    # ── what the annotators respond to: their yes-rate by the agent's severity label ──
    print("\n== annotator-majority 'deserved' rate by the agent's severity label (130 three-way signals) ==")
    for sev in ("P0", "P1", "P2", "P3"):
        xs = [r for r in recs if r["annot"] is not None and r["severity"] == sev]
        print(f"  {sev}: {sum(r['annot'] for r in xs)}/{len(xs)}" + (f" = {sum(r['annot'] for r in xs)/len(xs):.0%}" if xs else ""))
    # unattached-trigger dossiers: does the label leak favour the agent? compare their unrouted rate to the base rate
    unatt = [r for r in recs if run[r["signal_id"]]["facts"].get("account_triggers_unattached")]
    print(f"  dossiers with a written trigger on the account the agent never attached: {len(unatt)}; "
          f"not routed by agent: {sum(not r['agent_routed'] for r in unatt)} ({sum(not r['agent_routed'] for r in unatt)/len(unatt):.0%}) "
          f"vs corpus {sum(not r['agent_routed'] for r in recs)/len(recs):.0%}")

    # ── ordering test: same routed count, different keys ──────────────────────
    _, policy = apply_policy(recs, KEYS["tiered"], FLOOR["tiered"])
    n = len(policy)
    print(f"\n== ordering test: global top-{n} by each key (no cap, no floor) — isolates the ordering ==")
    print(HEADER)
    for name, key in KEYS.items():
        top = {r["signal_id"] for r in sorted(recs, key=key)[:n]}
        print(fmt(summarize(name, recs, top)))
    rnd = []
    for seed in range(200):
        rng = random.Random(seed)
        top = {r["signal_id"] for r in rng.sample(recs, n)}
        rnd.append(summarize("random", recs, top))
    print(f"{'random (mean/200)':<16}{n:>7}  {statistics.mean(x['prec_vs_deserved'] for x in rnd):>6.2f} "
          f"{statistics.mean(x['rec_vs_deserved'] for x in rnd):>6.2f}   {statistics.mean(x['prec_vs_annot'] for x in rnd):>6.2f} "
          f"{statistics.mean(x['rec_vs_annot'] for x in rnd):>6.2f}           {statistics.mean(x['churn_rate'] for x in rnd):>5.0%}"
          f"            ${statistics.mean(x['lost_arr_captured'] for x in rnd)/-1e6:>5.2f}M {statistics.mean(x['lost_arr_share'] for x in rnd):>4.0%}")

    # ── the policies, under the cap ───────────────────────────────────────────
    print(f"\n== policies under the {BUDGET}-per-owner-week cap ==")
    print(HEADER)
    print(fmt(summarize("agent (actual)", recs, agent)))
    print(fmt(summarize("tiered+floor", recs, policy)))
    _, des_only = apply_policy(recs, KEYS["tiered"], lambda r: r["tier"] == 1)
    print(fmt(summarize("tier-1 only", recs, des_only)))
    for name, key in KEYS.items():
        _, top = apply_policy(recs, key)
        print(fmt(summarize(f"{name} top-{BUDGET}", recs, top)))

    # ── per-owner view of the proposed policy ─────────────────────────────────
    print("\n== per-owner (CSMs): slots used vs available, precision vs annotator majority, lift over owner base rate ==")
    print(f"{'owner':<7}{'role':<5}{'cands':>6}{'routed':>7}{'slots':>6}  {'churn|routed':>13} {'churn|all':>10}  {'ann P':>6} {'ann base':>9}")
    weeks = sorted({r["week"] for r in recs})
    for oid in sorted({r["owner_id"] for r in recs}):
        rs = [r for r in recs if r["owner_id"] == oid]
        R = [r for r in rs if r["signal_id"] in policy]
        def churn(xs):
            k = [x for x in xs if x["outcome"] and x["outcome"] != "pending"]
            return f"{sum(x['outcome'] in BAD for x in k)/len(k):.0%}({len(k)})" if k else "-"
        ann = [r for r in rs if r["annot"] is not None]
        annR = [r for r in R if r["annot"] is not None]
        ap = f"{sum(r['annot'] for r in annR)/len(annR):.0%}" if annR else "-"
        ab = f"{sum(r['annot'] for r in ann)/len(ann):.0%}({len(ann)})" if ann else "-"
        print(f"{oid:<7}{owners[oid]['role']:<5}{len(rs):>6}{len(R):>7}{len(weeks)*BUDGET:>6}  {churn(R):>13} {churn(rs):>10}  {ap:>6} {ab:>9}")

    # ── Q4: what the policy leaves unrouted ───────────────────────────────────
    print("\n== Q4: missed risk — signals the policy does NOT route that went on to churn or downgrade ==")
    routed_accts = {r["account_id"] for r in recs if r["signal_id"] in policy}
    missed = [r for r in recs if r["signal_id"] not in policy and r["outcome"] in BAD and r["account_id"] not in routed_accts]
    lost_by_acct = worst_loss(recs)
    print(f"  accounts that churned/downgraded with NO signal routed by the policy: {len({r['account_id'] for r in missed})} "
          f"({len(missed)} signals), lost ARR ${sum(v for a, v in lost_by_acct.items() if a in {r['account_id'] for r in missed})/-1e6:.2f}M "
          f"of ${sum(lost_by_acct.values())/-1e6:.2f}M across {len(lost_by_acct)} accounts")
    print("  by attribution:", {k: (v, f"${sum(r['arr_delta'] for r in missed if r['attribution']==k)/-1e6:.2f}M")
                                for k, v in Counter(r["attribution"] for r in missed).items()})
    print("  with concurrent interventions:", sum(1 for r in missed if r["concurrent"]),
          "  agent had routed:", sum(1 for r in missed if r["agent_routed"]),
          "  tier:", dict(sorted(Counter(r["tier"] for r in missed).items())))
    agent_accts = {r["account_id"] for r in recs if r["agent_routed"]}
    only_agent = {a: v for a, v in lost_by_acct.items() if a in agent_accts and a not in routed_accts}
    only_policy = {a: v for a, v in lost_by_acct.items() if a in routed_accts and a not in agent_accts}
    neither = {a: v for a, v in lost_by_acct.items() if a not in routed_accts and a not in agent_accts}
    print(f"  churned/downgraded accounts the agent reached and the policy would not: {len(only_agent)} (${sum(only_agent.values())/-1e6:.2f}M)")
    print(f"  the reverse, policy reaches and the agent did not: {len(only_policy)} (${sum(only_policy.values())/-1e6:.2f}M)")
    print(f"  neither reached: {len(neither)} (${sum(neither.values())/-1e6:.2f}M)")
    # attribution == certain anywhere
    cert = [r for r in recs if r["attribution"] == "certain"]
    print(f"  attribution=certain: {len(cert)} outcomes; policy routed {sum(r['signal_id'] in policy for r in cert)}, agent routed {sum(r['agent_routed'] for r in cert)}")

    # ── review checks (2026-09-17): numbers the two reviews asked for, computed as they specified ──
    print("\n== review checks ==")
    both = policy & agent
    only_policy = policy - agent
    print(f"routed by both agent and policy: {len(both)}; by policy only: {len(only_policy)} (complaint/wasted fields unobservable there: "
          f"{sum(r['complained'] or r['wasted'] for r in recs if r['signal_id'] in only_policy)} carry either)")
    def cw(ids):
        xs = [r for r in recs if r["signal_id"] in ids]
        return f"complaints {sum(r['complained'] for r in xs)}/{len(xs)} = {sum(r['complained'] for r in xs)/len(xs):.1%}, wasted {sum(r['wasted'] for r in xs)}/{len(xs)} = {sum(r['wasted'] for r in xs)/len(xs):.1%}"
    print(f"  on the {len(both)} overlap signals: {cw(both)}")
    print(f"  agent's 294: {cw(agent)}")
    print(f"  agent-routed signals the policy drops ({len(agent - policy)}): {cw(agent - policy)}")
    # spec-only yardstick (§8.1 + §4.6), no §6.3
    spec_only = {r["signal_id"] for r in recs if r["deserved"] and not str(r["deserved_reason"]).startswith("§6.3")}
    tp = len(agent & spec_only)
    print(f"  agent vs §8.1+§4.6 only ({len(spec_only)} signals): precision {tp}/{len(agent)} = {tp/len(agent):.0%}, recall {tp}/{len(spec_only)} = {tp/len(spec_only):.0%}")
    by_reason = Counter(str(r["deserved_reason"]).split(" ")[0] for r in recs if r["deserved"])
    for k in ("§8.1", "§4.6", "§6.3"):
        ids = {r["signal_id"] for r in recs if r["deserved"] and str(r["deserved_reason"]).startswith(k)}
        print(f"  agent routed {len(ids & agent)} of {len(ids)} {k}-deserved signals")
    # agent-score ordering at top-189: recall vs tier 1
    top_as = {r["signal_id"] for r in sorted(recs, key=KEYS["agent-score"])[:len(policy)]}
    t1 = {r["signal_id"] for r in recs if r["tier"] == 1}
    print(f"  agent-score top-{len(policy)} contains {len(top_as & t1)} of {len(t1)} tier-1 signals ({len(top_as & t1)/len(t1):.0%})")
    # §8.1 trigger kinds
    kinds = Counter()
    multi = 0
    for r in recs:
        if r["deserved"] and str(r["deserved_reason"]).startswith("§8.1"):
            ks = run[r["signal_id"]]["facts"]["trigger_source"]["confirmed"]
            if len(ks) > 1: multi += 1
            else: kinds[ks[0]] += 1
    print(f"  §8.1 single-trigger kinds: {dict(kinds)}; more than one trigger: {multi}")
    # arr_at_risk wrong: M1 ∪ M5 dossiers
    m15 = {r["signal_id"] for r in recs if {v["rule"] for v in run[r["signal_id"]]["result"]["violations"]} & {"M1", "M5"}}
    print(f"  arr_at_risk provably wrong (M1 ∪ M5): {len(m15)} dossiers")
    # below floor among routed
    below = [r for r in recs if r["signal_id"] in policy and run[r["signal_id"]]["facts"].get("acct_tier") is not None
             and (d0 := next(d for d in dossiers if d["signal_id"] == r["signal_id"])) and
             ((d0.get("scoring") or {}).get("arr_at_risk") or 0) < (d0["metadata"].get("materiality_floor") or 0)]
    print(f"  policy-routed signals with arr_at_risk below the materiality floor: {len(below)} of {len(policy)}; "
          f"agent-routed below floor: {sum(1 for d in dossiers if (d.get('decision') or {}).get('disposition') in ('routed','acknowledged') and ((d.get('scoring') or {}).get('arr_at_risk') or 0) < (d['metadata'].get('materiality_floor') or 0))}")
    # unattached-trigger dossiers already deserved
    print(f"  unattached-trigger dossiers already deserved by another condition: {sum(r['deserved'] for r in unatt)} of {len(unatt)}")
    # owner-weeks by role
    print(f"  owner-weeks: CSM {sum(1 for k in by if owners[k[0]]['role']=='csm')}, AE {sum(1 for k in by if owners[k[0]]['role']=='ae')}; "
          f"policy routes CSM-owned {sum(1 for r in recs if r['signal_id'] in policy and owners[r['owner_id']]['role']=='csm')}, AE-owned {sum(1 for r in recs if r['signal_id'] in policy and owners[r['owner_id']]['role']=='ae')}")
    # policy routes inside burst weeks
    bw = {wk for wk, ds in byweek.items() if len(ds) >= BURST_MIN}
    print(f"  policy routes {sum(1 for r in recs if r['signal_id'] in policy and r['week'] in bw)} signals inside the {len(bw)} burst weeks: "
          f"{ {wk: sum(1 for r in recs if r['signal_id'] in policy and r['week']==wk) for wk in sorted(bw)} }")
    print(f"  tier-2 signals inside burst weeks: {sum(1 for r in recs if r['tier']==2 and r['week'] in bw)} of {sum(1 for r in recs if r['tier']==2)}")
    # W18: benign-labelled and routed; M6 breakdown; artefact reasons for W21/W24
    for wk in ("2026-W18", "2026-W21", "2026-W24"):
        ds = byweek[wk]
        ben = [d for d in ds if (d.get("hypotheses") or [{}])[0].get("hypothesis") == "benign_variation"]
        cs = Counter(st for d in ds for st in (run[d["signal_id"]]["facts"].get("claim_status") or []))
        expl = [v["explanation"] for d in ds for v in run[d["signal_id"]]["result"]["violations"] if v["rule"] == "M6" and "only on uncorrected" in v["explanation"]]
        print(f"  {wk}: benign-labelled {len(ben)}, of which routed {sum((d.get('decision') or {}).get('disposition') in ('routed','acknowledged') for d in ben)}; "
              f"claim_status {dict(cs)}; artefact explanations citing 'legacy' {sum('legacy' in e for e in expl)}, citing 'ingest' {sum('ingest' in e for e in expl)}, "
              f"metrics {dict(Counter(m.get('metric') for d in ds for m in d.get('metrics_claimed') or []))}")
    # duplicate artefacts among routed pairs in one owner-week
    dup = 0
    for (oid, wk), rs in by.items():
        routed_here = [r for r in rs if r["signal_id"] in policy]
        arts = Counter(a for r in routed_here for a in {e.get("artifact_id") for e in next(d for d in dossiers if d["signal_id"] == r["signal_id"]).get("evidence") or []})
        dup += sum(1 for a, c in arts.items() if c > 1)
    print(f"  owner-weeks where two policy-routed signals share an evidence artefact: {dup} shared artefacts")

    # ── Q1 telemetry evidence, same weekday one week earlier, dedupe latest ingested_at, ingest_status ok ──
    print("\n== Q1 telemetry: regional median dau_seats, holiday vs same weekday one week earlier ==")
    tel = jl(DATA / "telemetry.jsonl")
    best = {}
    for t in tel:
        k = (t["account_id"], t["date"])
        if k not in best or t["ingested_at"] > best[k]["ingested_at"]:
            best[k] = t
    def regmed(region, day, metric="dau_seats"):
        v = [t[metric] for (a, dd), t in best.items() if dd == day and accounts[a]["region"] == region
             and t.get("ingest_status") == "ok" and t.get(metric) is not None]
        return statistics.median(v) if v else float("nan")
    HOL = [("Good Friday Apr 3", "2026-04-03", "2026-03-27"), ("Easter Monday Apr 6", "2026-04-06", "2026-03-30"),
           ("Labour Day May 1", "2026-05-01", "2026-04-24"), ("Memorial Day May 25", "2026-05-25", "2026-05-18"),
           ("Jul 3 (Independence Day observed)", "2026-07-03", "2026-06-26")]
    print(f"{'holiday':<36}{'namer':>16}{'emea':>16}{'latam':>16}   {'namer err%':>12}{'namer p95':>14}")
    for name, d1, d0 in HOL:
        cells = "".join(f"{regmed(rg, d0):>7.1f}→{regmed(rg, d1):<7.1f}" for rg in ("namer", "emea", "latam"))
        print(f"{name:<36}{cells}   {regmed('namer', d0, 'error_rate_pct'):>5.2f}→{regmed('namer', d1, 'error_rate_pct'):<5.2f}"
              f"{regmed('namer', d0, 'query_p95_ms'):>6.0f}→{regmed('namer', d1, 'query_p95_ms'):<6.0f}")
    print("  namer weekly weekday medians (Mon–Fri pooled), weeks of Jun 15 → Jul 20:",
          [round(statistics.median([t["dau_seats"] for (a, dd), t in best.items() if accounts[a]["region"] == "namer" and t.get("ingest_status") == "ok"
                                     and date.fromisoformat(dd).weekday() < 5 and 0 <= (date.fromisoformat(dd) - date(2026, 6, 15) - __import__("datetime").timedelta(days=7 * i)).days < 7]), 1)
           for i in range(6)])
    # holiday / OOO artefacts on the burst-week accounts within ±14 days of the sweep
    import re
    arts = jl(DATA / "artifacts.jsonl")
    pat = re.compile(r"holiday|out of office|\booo\b|public holiday|bank holiday|long weekend", re.I)
    hol_arts = [a for a in arts if pat.search(a.get("text") or "")]
    print(f"  holiday/OOO artefacts corpus-wide: {len(hol_arts)}")
    for wk in ("2026-W14", "2026-W18", "2026-W22", "2026-W27"):
        ds = byweek[wk]
        sweep = max(date.fromisoformat(d["opened_at"][:10]) for d in ds)
        accts_wk = {d["account_id"] for d in ds}
        hit = {a["account_id"] for a in hol_arts if a["account_id"] in accts_wk and abs((date.fromisoformat(a["timestamp"][:10]) - sweep).days) <= 14}
        ex = next((a for a in hol_arts if a["account_id"] in accts_wk and abs((date.fromisoformat(a["timestamp"][:10]) - sweep).days) <= 14 and a.get("author_type") == "customer"), None)
        print(f"  {wk}: {len(hit)} of {len(accts_wk)} accounts have a holiday/OOO artefact within ±14 days"
              + (f"; e.g. {ex['artifact_id']} ({ex['timestamp'][:10]}): \"{(ex['text'] or '')[:90]}\"" if ex else ""))

    # ── write the ranking ─────────────────────────────────────────────────────
    rank, _ = apply_policy(recs, KEYS["tiered"], FLOOR["tiered"])
    out = ROOT / "analysis" / "attention_budget_ranking.csv"
    cols = ["owner_id", "week", "rank", "routed", "signal_id", "account_id", "detector", "tier", "deserved_reason", "severity",
            "days_to_renewal", "arr_annual", "agent_routed", "outcome", "arr_delta", "attribution"]
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in sorted(recs, key=lambda r: (r["owner_id"], r["week"], rank[r["signal_id"]])):
            w.writerow([r["owner_id"], r["week"], rank[r["signal_id"]], r["signal_id"] in policy] +
                       [r[c] for c in cols[4:]])
    print(f"\nwrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
