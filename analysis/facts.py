"""
ABOUTME: Builds analysis/facts.csv — one row per dossier with every verified fact the evaluator
ABOUTME: extracts, joined to outcomes and annotator labels. No scores; raw material for the rubric.
"""

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval import RULES, SignalEvaluator  # noqa: E402
from signal_eval.spec import RANK, TELEMETRY_METRICS, TTA_HOURS  # noqa: E402
from signal_eval.util import pct  # noqa: E402

OUT = Path(__file__).resolve().parent / "facts.csv"
RULE_IDS = [r[0] for r in RULES]


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def main():
    D = E._load("signal_dossiers.jsonl")
    A = E._load("artifacts.jsonl")
    ev = SignalEvaluator(label_cache_path=E.LABEL_CACHE if E.LABEL_CACHE.exists() else None)
    ev.load_context(E._load("accounts.jsonl"), E._load("owners.jsonl"), E._load("telemetry.jsonl"), A, D)
    arts = ev.cx.artifacts
    outcomes = {o["signal_id"]: o for o in E._load("outcomes.jsonl")}
    ann = {i: {r["signal_id"]: r for r in E._load(f"annotations/annotator_{i}.jsonl")} for i in (1, 2, 3)}

    rows = []
    for d in D:
        r = ev.explain(d)
        acc = ev.cx.accounts[d["account_id"]]
        own = ev.cx.owners[d["metadata"]["owner_id"]]
        o = outcomes.get(d["signal_id"], {})
        opened = ts(d["opened_at"])
        rule_hits = Counter(v["rule"] for v in r["violations"])
        worst = {}
        for v in r["violations"]:
            worst[v["rule"]] = max(worst.get(v["rule"], 0), v["severity"])

        # evidence provenance (independent of evaluator)
        ev_rows = []
        for e in d["evidence"]:
            a = arts.get(e["artifact_id"])
            if a is None:
                ev_rows.append({"exists": False})
                continue
            hay = (a.get("subject") or "") + "\n" + a["text"]
            ev_rows.append({
                "exists": True,
                "same_account": a["account_id"] == d["account_id"],
                "verbatim": e["quote"] in hay,
                "author_type": a["author_type"],
                "type": a["type"],
                "restricted": a["restricted"],
                "age_days": abs((opened - ts(a["timestamp"])).days),
                "mentions_other": bool(a.get("mentions_other_account")),
                "is_bot": a["author_type"] == "bot",
            })
        verified = [x for x in ev_rows if x.get("exists") and x["same_account"] and x["verbatim"]]
        cust = [x for x in verified if x["author_type"] == "customer"]

        h = d["hypotheses"][0] if d["hypotheses"] else {}
        refs_not_attached = len(set(h.get("evidence_refs", [])) - {e["artifact_id"] for e in d["evidence"]})
        life_edges = [(e["from_state"], e["to_state"]) for e in d["lifecycle"]]
        notes = d["notifications"]
        acts = Counter(a["action"] for a in d["actions"])
        first_notify_h = (ts(notes[0]["at"]) - opened).total_seconds() / 3600 if notes else None
        closed = ts(d["closed_at"]) if d["closed_at"] else None

        row = {
            # identity
            "signal_id": d["signal_id"], "account_id": d["account_id"], "detector": d["detector"],
            "detector_version": d["detector_version"], "opened_at": d["opened_at"][:10],
            "week": opened.strftime("%G-W%V"), "owner_id": d["metadata"]["owner_id"],
            # account
            "tier": acc["tier"], "region": acc["region"], "industry": acc["industry"],
            "arr_annual": acc["arr_annual"], "floor": acc["materiality_floor"], "collector": acc["collector"],
            "flags": "|".join(acc["flags"]), "restricted_account": bool({"legal_hold", "mna_quiet_period"} & set(acc["flags"])),
            "days_to_renewal": d["metadata"]["days_to_renewal"],
            "metadata_mismatch": "|".join(r["_facts"].get("context_loss") or []),
            # agent decision
            "disposition": d["decision"]["disposition"], "play": d["decision"]["recommended_play"],
            "customer_visible": d["decision"]["customer_visible"],
            "severity": d["scoring"]["severity"], "confidence": d["scoring"]["confidence"],
            "arr_at_risk": d["scoring"]["arr_at_risk"], "below_floor": d["scoring"]["arr_at_risk"] < acc["materiality_floor"],
            "hypothesis": h.get("hypothesis"), "hyp_confidence": h.get("confidence"),
            # lifecycle shape
            "n_steps": len(d["lifecycle"]), "final_state": d["lifecycle"][-1]["to_state"] if d["lifecycle"] else None,
            "skipped_enrichment": ("hypothesis_formed", "scored") in life_edges,
            "went_backward": any(RANK.get(t, 99) < RANK.get(f, -1) for f, t in life_edges if f in RANK and t in RANK),
            "enrichment_timeout": any(e["trigger"] == "enrichment_timeout" for e in d["lifecycle"]),
            "human_preempt": any(e["trigger"] == "human_preempt" for e in d["lifecycle"]),
            "hours_open_to_close": round((closed - opened).total_seconds() / 3600, 1) if closed else None,
            # actions / notifications
            "n_attach": acts["attach_evidence"], "n_request_enrichment": acts["request_enrichment"],
            "n_notify_actions": acts["notify_owner"], "n_notifications": len(notes),
            "first_notify_hours": round(first_notify_h, 1) if first_notify_h is not None else None,
            "tta_target_h": TTA_HOURS[d["scoring"]["severity"]],
            "owner_tz": own["timezone"], "owner_channel": own["preferred_channel"], "owner_locale": own["locale"],
            # evidence facts
            "n_evidence": len(d["evidence"]), "n_verified": len(verified),
            "n_missing_artifact": sum(1 for x in ev_rows if not x.get("exists")),
            "n_other_account": sum(1 for x in ev_rows if x.get("exists") and not x["same_account"]),
            "n_not_verbatim": sum(1 for x in ev_rows if x.get("exists") and x["same_account"] and not x["verbatim"]),
            "n_restricted_quoted": sum(1 for x in ev_rows if x.get("restricted") and x.get("exists")),
            "n_bot": sum(1 for x in ev_rows if x.get("is_bot")),
            "n_customer_verified": len(cust),
            "customer_types": "|".join(sorted({x["type"] for x in cust})),
            "newest_customer_age_days": min((x["age_days"] for x in cust), default=None),
            "n_mentions_other": sum(1 for x in ev_rows if x.get("mentions_other")),
            "distinct_sources": len({x["type"] for x in verified if not x["is_bot"]}),
            "refs_not_attached": refs_not_attached,
            # claims
            "n_claims": len([m for m in d["metrics_claimed"] if m["metric"] in TELEMETRY_METRICS]),
            "claim_status": "|".join(r["_facts"]["claim_status"]),
            "claim_metrics": "|".join(m["metric"] for m in d["metrics_claimed"] if m["metric"] in TELEMETRY_METRICS),
            "claim_pcts": "|".join(str(pct(m["claim"])) for m in d["metrics_claimed"] if m["metric"] in TELEMETRY_METRICS),
            "cohort_key": (r["_facts"].get("cohort_match") or {}).get("key", ""),
            "n_arr_restatements": len([m for m in d["metrics_claimed"] if m["metric"] == "arr_at_risk"]),
            # triggers (structural + model, asymmetric) and account-level misses
            "triggers": "|".join(r["_facts"]["triggers"]),
            "trigger_proxy_only": bool((r["_facts"]["trigger_source"] or {}).get("proxy")),
            "triggers_added_by_model": "|".join((r["_facts"]["trigger_source"] or {}).get("added_by_model", [])),
            "triggers_removed_by_model": "|".join((r["_facts"]["trigger_source"] or {}).get("removed_by_model", [])),
            "account_trigger_unattached": "|".join(sorted({t for _, _, ts_ in r["_facts"]["account_triggers_unattached"] for t in ts_})),
            "account_trigger_artifacts": "|".join(aid for _, aid, _ in r["_facts"]["account_triggers_unattached"]),
            "n_stale_evidence": r["_facts"]["n_stale_evidence"],
            "evidence_topics": "|".join(f"{k}:{v}" for k, v in r["_facts"]["evidence_topics"].items()),
            "hypothesis_text_support": r["_facts"]["hypothesis_text_support"],
            "security_review_present": r["_facts"]["security_review"],
            # duplicates
            "dup_same_detector_7d": rule_hits["Q5"] > 0,
            # outcomes
            "renewal_outcome": o.get("renewal_outcome"), "arr_delta": o.get("arr_delta"),
            "attribution": o.get("attribution"), "reached_human": o.get("reached_human"),
            "owner_acted": o.get("owner_acted"), "owner_marked_useful": o.get("owner_marked_useful"),
            "complained": o.get("customer_complained_about_outreach"), "wasted": o.get("escalation_was_wasted"),
            "post_hoc_root_cause": o.get("post_hoc_root_cause"),
            "concurrent_interventions": "|".join(o.get("concurrent_interventions") or []),
        }
        # one column per rule: worst severity (0 = not flagged)
        for rid in RULE_IDS:
            row[f"v_{rid}"] = worst.get(rid, 0)
        # annotators
        for i in (1, 2, 3):
            a = ann[i].get(d["signal_id"])
            row[f"ann{i}_deserved"] = a["deserved_attention"] if a else None
            row[f"ann{i}_quality"] = a["quality_score"] if a else None
            row[f"ann{i}_cats"] = "|".join(sorted({f["category"] for f in a["failure_points"]})) if a else None
            row[f"ann{i}_flags"] = "|".join(sorted(a["risk_flags"])) if a else None
        rows.append(row)

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows × {len(rows[0])} columns → {OUT}")
    print("columns:", ", ".join(rows[0].keys()))


if __name__ == "__main__":
    main()
