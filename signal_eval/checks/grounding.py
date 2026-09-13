"""
ABOUTME: Quantitative grounding — spec §9 M6. Recomputes every metric claim from the corrected
ABOUTME: telemetry and labels it grounded / cohort / artifact / wrong / unverifiable.
"""

from datetime import timedelta

from ..spec import COHORT_MATCH_PP, COHORT_MIN_ACCOUNTS, COHORT_MIN_DROP_PCT, GROUNDING_TOL_PP, TELEMETRY_METRICS, violation
from ..util import day, mean, num, pct

# spec §9 M6: "to within 5 percentage points" — the tolerance is GROUNDING_TOL_PP exactly, with no
# rounding allowance on top; a claim 5.4pp off the corrected telemetry is not grounded.


def _raw_sum(rows, metric, end, w, af, cx):
    """What a naive reader gets: every row present, no event corrections, missing days = zero."""
    tot = 0.0
    for i in range(w):
        dt = end - timedelta(days=i)
        r = rows.get(dt)
        if r is None:
            continue
        v = r[metric]
        if metric == "api_calls" and af["collector"] == "legacy" and dt < cx.legacy_end:
            v = v * 2
        if metric == "query_p95_ms" and dt < cx.p95_step:
            v = v / cx.p95_factor
        tot += v
    return tot


def _events(metric, end, w, af, cx, a_miss, b_miss, bad_reasons):
    events = []
    span_lo = end - timedelta(days=2 * w)
    if metric == "api_calls" and af["collector"] == "legacy" and span_lo < cx.legacy_end <= end:
        events.append(f"legacy double-count ended {cx.legacy_end}")
    if metric == "query_p95_ms" and span_lo < cx.p95_step <= end:
        events.append(f"p95 instrumentation step {cx.p95_step}")
    if af["region"] in cx.ingest_gap_regions and (a_miss + b_miss) and span_lo <= cx.ingest_gap[1] and cx.ingest_gap[0] <= end:
        events.append(f"ingest gap {cx.ingest_gap[0]}→{cx.ingest_gap[1]} ({a_miss + b_miss} missing days)")
    elif a_miss + b_miss:
        events.append(f"{a_miss + b_miss} missing day(s) read as zero")
    for why, n in bad_reasons.items():
        events.append(f"{n} day(s) {why}")
    return events


def check_grounding(d, ctx, cx):
    out, statuses, detail = [], [], []
    rows = cx.tel.get(d.get("account_id"), {})
    af = ctx["acct"]
    for m in d.get("metrics_claimed") or []:
        metric = m.get("metric")
        if metric not in TELEMETRY_METRICS:
            continue
        claimed = pct(m.get("claim"))
        w, end = int(num(m.get("window_days")) or 7), day(m.get("as_of"))
        rec = {"step": m.get("step"), "metric": metric, "claimed_pct": claimed, "as_of": m.get("as_of"), "window_days": w}
        detail.append(rec)
        if claimed is None or end is None:
            rec.update(status="unverifiable", why="claim has no percentage or date")
            statuses.append("unverifiable")
            continue
        after, a_miss, a_bad, a_why = cx.window(rows, end, w)
        before, b_miss, b_bad, b_why = cx.window(rows, end - timedelta(days=w), w)

        raw_b, raw_a = _raw_sum(rows, metric, end - timedelta(days=w), w, af, cx), _raw_sum(rows, metric, end, w, af, cx)
        raw_pct = (raw_a - raw_b) / raw_b * 100 if raw_b else None
        raw_ok = raw_pct is not None and abs(raw_pct - claimed) <= GROUNDING_TOL_PP
        rec["raw_pct"] = None if raw_pct is None else round(raw_pct, 1)

        events = _events(metric, end, w, af, cx, a_miss, b_miss, a_why + b_why)
        # docs/data_dictionary.md: a backfill row is the correction that survives the M6 dedup, so it stays
        # usable — but the writeup must be able to say how many of the 2w days rest on corrected rows.
        backfill = sum(1 for i in range(2 * w) if (r := rows.get(end - timedelta(days=i))) and r.get("ingest_status") == "backfill")
        rec.update(events=events, missing_days=a_miss + b_miss, bad_days=a_bad + b_bad, backfill_days=backfill)

        # spec M6: exclude *windows* whose ingest is not ok — a contaminated window cannot ground a claim.
        # It is an *artefact* only if the clean days that remain disagree with the claim; if they agree,
        # the claim is merely unverifiable under the spec's own rule.
        if a_bad + b_bad or len(after) < max(1, w // 2) or len(before) < max(1, w // 2):
            clean_pct = None
            if after and before and mean([r[metric] for r in before]):
                cb_, ca_ = mean([r[metric] for r in before]), mean([r[metric] for r in after])
                clean_pct = (ca_ - cb_) / cb_ * 100
                rec["corrected_pct"] = round(clean_pct, 1)
            clean_agrees = clean_pct is not None and abs(clean_pct - claimed) <= GROUNDING_TOL_PP   # spec §9 M6: 5pp
            if raw_ok and not clean_agrees:
                rec["status"] = "artifact"
                statuses.append("artifact")
                out.append(violation(m["step"], "M6", f"{metric} {claimed:+.0f}% reproduces only on uncorrected data"
                                     + (f" (clean days {clean_pct:+.0f}%)" if clean_pct is not None else "") + " — " + "; ".join(events)))
            else:
                rec["status"] = "unverifiable"
                statuses.append("unverifiable")
                out.append(violation(m["step"], "M6", f"{metric} {claimed:+.0f}% cannot be verified under M6 (window not clean): "
                                     + ("; ".join(events) or f"{len(before)}/{len(after)} clean days")
                                     + (" — consistent on the clean days" if clean_agrees else ""), 0.4))
            continue
        b_mean, a_mean = mean([r[metric] for r in before]), mean([r[metric] for r in after])
        if not b_mean:
            rec.update(status="unverifiable", why="zero baseline")
            statuses.append("unverifiable")
            continue
        real = (a_mean - b_mean) / b_mean * 100
        rec["corrected_pct"] = round(real, 1)
        if abs(real - claimed) <= GROUNDING_TOL_PP:                        # spec §9 M6: within 5pp, exactly
            # spec §9 M6 arithmetic: a rate can fall to zero (−100%) but never below it; only < −100 is impossible
            if claimed < -100:
                out.append(violation(m["step"], "M6", f"{metric} {claimed:+.0f}% is arithmetically impossible", 0.8))
            # real for this account — but is it real for the whole region on the same days?
            coh = cx.cohort.get(af["region"]) or cx.cohort.get("ALL") or {}
            days_a = [end - timedelta(days=i) for i in range(w) if (end - timedelta(days=i)) in coh]
            days_b = [end - timedelta(days=w + i) for i in range(w) if (end - timedelta(days=w + i)) in coh]
            enough = all(coh[x].get("_n", 0) >= COHORT_MIN_ACCOUNTS for x in days_a + days_b)
            ca = [coh[x][metric] for x in days_a]
            cb = [coh[x][metric] for x in days_b]
            if enough and ca and cb and mean(cb):
                coh_pct = (mean(ca) - mean(cb)) / mean(cb) * 100
                rec["cohort_pct"] = round(coh_pct, 1)
                if coh_pct <= -COHORT_MIN_DROP_PCT and abs(coh_pct - real) <= COHORT_MATCH_PP:
                    rec["status"] = "cohort"
                    statuses.append("cohort")
                    ctx.setdefault("cohort_notes", []).append(
                        f"{metric} {claimed:+.0f}% matches region {af['region']} median {coh_pct:+.0f}% over the same window (calendar / cohort event)")
                    continue
            rec["status"] = "grounded"
            statuses.append("grounded")
            continue
        if raw_ok and events:
            rec["status"] = "artifact"
            statuses.append("artifact")
            out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}%, corrected telemetry {real:+.0f}%; reproduces only on uncorrected data — " + "; ".join(events)))
        else:
            rec["status"] = "wrong"
            statuses.append("wrong")
            raw_txt = f" (raw {raw_pct:+.0f}%)" if raw_pct is not None else ""
            out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}%, corrected telemetry {real:+.0f}%{raw_txt}", 0.8 if claimed < -100 else 1.0))   # spec §9 M6: −100 is possible
    ctx.update(claim_status=statuses, claim_detail=detail)
    return out
