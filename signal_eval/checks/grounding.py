"""
ABOUTME: Quantitative grounding — spec §9 M6. Recomputes every metric claim from the corrected telemetry as a
ABOUTME: paired-day change and labels it grounded / artifact / wrong / unverifiable, with the cohort move as a fact.
"""

from datetime import timedelta

from ..context import paired_change
from ..spec import (COHORT_KEYS, COHORT_MATCH_PP, COHORT_MIN_ACCOUNTS, COHORT_MIN_DROP_PCT, GROUNDING_TOL_PP,
                    MIN_PAIRS, TELEMETRY_METRICS, violation)
from ..util import day, num, pct

# spec §9 M6: "to within 5 percentage points" — the tolerance is GROUNDING_TOL_PP exactly, with no
# rounding allowance on top; a claim 5.4pp off the corrected telemetry is not grounded.


def _raw_sum(rows, metric, end, w, af, cx):
    """What a naive reader gets: every row present, no event corrections, missing days = zero."""
    tot = 0.0
    for i in range(w):
        dt = end - timedelta(days=i)
        r = rows.get(dt)
        if r is None or r.get(metric) is None:
            continue
        v = r[metric]
        if metric == "api_calls" and af["collector"] == "legacy" and dt < cx.legacy_end:
            v = v * 2
        if metric == "query_p95_ms" and dt < cx.p95_step:
            v = v / cx.p95_factor
        tot += v
    return tot


def _events(metric, end, w, af, cx, rows, excluded):
    """The documented data events (docs/domain.md) and the bad rows that touch the 2w-day span, named."""
    events = []
    span_lo = end - timedelta(days=2 * w)
    if metric == "api_calls" and af["collector"] == "legacy" and span_lo < cx.legacy_end <= end:
        events.append(f"legacy double-count ended {cx.legacy_end}")
    if metric == "query_p95_ms" and span_lo < cx.p95_step <= end:
        events.append(f"p95 instrumentation step {cx.p95_step}")
    missing = [end - timedelta(days=i) for i in range(2 * w) if (end - timedelta(days=i)) not in rows]
    in_gap = [d for d in missing if af["region"] in cx.ingest_gap_regions and cx.ingest_gap[0] <= d <= cx.ingest_gap[1]]
    if in_gap:
        events.append(f"ingest gap {cx.ingest_gap[0]}→{cx.ingest_gap[1]} ({len(in_gap)} missing days)")
    if len(missing) - len(in_gap):
        events.append(f"{len(missing) - len(in_gap)} missing day(s) read as zero")
    for why, n in excluded.items():
        if why != "missing":
            events.append(f"{n} day(s) {why}")
    return events


def _cohort_match(cx, af, d, metric, end, w, paired):
    """First cohort (industry, then region) whose leave-one-out median moves like this account."""
    for key in COHORT_KEYS:
        value = (cx.accounts.get(d.get("account_id")) or {}).get(key, af.get(key))
        if value is None:
            continue
        med, n = cx.cohort_change(metric, end, w, key, value, d.get("account_id"))
        if n >= COHORT_MIN_ACCOUNTS and med <= -COHORT_MIN_DROP_PCT and abs(med - paired) <= COHORT_MATCH_PP:
            return {"key": key, "value": value, "median_pct": round(med, 1), "n": n, "metric": metric}
    return None


def check_grounding(d, ctx, cx):
    out, statuses, detail = [], [], []
    rows = cx.tel.get(d.get("account_id"), {})
    af = ctx["acct"]
    for m in d.get("metrics_claimed") or []:
        metric = m.get("metric")
        if metric == "arr_at_risk":                       # a restatement of the agent's own figure: M5's business
            continue
        claimed = pct(m.get("claim"))
        w, end = num(m.get("window_days")), day(m.get("as_of"))
        w = int(w) if w and w > 0 else None
        rec = {"step": m.get("step"), "metric": metric, "claimed_pct": claimed, "as_of": m.get("as_of"), "window_days": w}
        detail.append(rec)
        # spec §9 M6: "every entry in metrics_claimed must be reproducible from telemetry.jsonl". A claim that cannot
        # even be attempted — unknown metric, no percentage, no date, no window — is unverifiable, and that is a
        # finding (uncertain), never silence (decision 7: silence cannot be read as a pass).
        why = ("not a telemetry metric" if metric not in TELEMETRY_METRICS else
               "no percentage in the claim" if claimed is None else
               "no as_of date" if end is None else
               "no window_days stated" if w is None else None)
        if why:
            rec.update(status="unverifiable", why=why)
            statuses.append("unverifiable")
            out.append(violation(m.get("step"), "M6", f"{metric} claim {m.get('claim')!r} cannot be verified under M6: {why}", certain=False))
            continue

        raw_b, raw_a = _raw_sum(rows, metric, end - timedelta(days=w), w, af, cx), _raw_sum(rows, metric, end, w, af, cx)
        raw_pct = (raw_a - raw_b) / raw_b * 100 if raw_b else None
        raw_ok = raw_pct is not None and abs(raw_pct - claimed) <= GROUNDING_TOL_PP

        p = paired_change(rows, metric, end, w)
        paired, n, min_pairs = p["pct"], p["n_pairs"], MIN_PAIRS(w)
        events = _events(metric, end, w, af, cx, rows, p["excluded"])
        span = [rows.get(end - timedelta(days=i)) for i in range(2 * w)]
        # docs/data_dictionary.md: a backfill row is the correction that survives the M6 dedup, so it stays
        # usable — but the writeup must be able to say how many of the 2w days rest on corrected rows.
        rec.update(raw_pct=None if raw_pct is None else round(raw_pct, 1), paired_pct=None if paired is None else round(paired, 1),
                   n_pairs=n, min_pairs=min_pairs, excluded=p["excluded"], events=events,
                   backfill_days=sum(1 for r in span if r and r.get("ingest_status") == "backfill"),
                   seats_overshoot_days=sum(1 for r in span if r and r.get("seats_overshoot")), cohort=None)
        ev_txt = "; ".join(events) or "no events"
        raw_txt = f"raw {raw_pct:+.1f}%" if raw_pct is not None else "raw n/a"

        if n < min_pairs:
            cover = f"{n}/{w} clean pairs (min {min_pairs})"
            # the days that survive cannot settle it either way; if the zero-filled sum reproduces the claim and
            # rows were lost, the claim most likely came from that sum (docs/domain.md) — uncertain artefact
            if raw_ok and p["excluded"]:
                rec["status"] = "artifact"
                out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}%: raw zero-filled sum reproduces the claim ({raw_txt}); "
                                     f"only {cover}, real change not computable — events: {ev_txt}", certain=False))
            else:
                rec["status"] = "unverifiable"
                out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}% cannot be verified under M6: {cover}"
                                     + (f" — events: {ev_txt}" if events else ""), certain=False))
            statuses.append(rec["status"])
            continue
        if paired is None:
            # the baseline window sums to zero, so no percentage change exists to reproduce; silence would
            # read as a pass, so M6 records the claim as unverifiable (uncertain), as for an unparseable claim
            rec.update(status="unverifiable", why="zero baseline")
            statuses.append("unverifiable")
            out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}% cannot be verified under M6: "
                                 f"the baseline window sums to zero ({raw_txt}) — events: {ev_txt}", certain=False))
            continue

        rec["cohort"] = _cohort_match(cx, af, d, metric, end, w, paired)
        if rec["cohort"] and not ctx.get("cohort_match"):
            ctx["cohort_match"] = rec["cohort"]
        pair_txt = f"paired {paired:+.1f}% on {n}/{w} pairs"
        if abs(paired - claimed) <= GROUNDING_TOL_PP:                      # spec §9 M6: within 5pp, exactly
            # spec §9 M6 arithmetic: a rate can fall to zero (−100%) but never below it; only < −100 is impossible
            if claimed < -100:
                out.append(violation(m["step"], "M6", f"{metric} {claimed:+.0f}% is arithmetically impossible", certain=False))
            rec["status"] = "grounded"
        elif raw_ok:
            kind = "inflated real decline" if paired <= -GROUNDING_TOL_PP else "manufactured decline"
            rec["status"] = "artifact"
            out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}% reproduces only on uncorrected data ({raw_txt}; "
                                 f"{pair_txt}) — {kind}; events: {ev_txt}"))
        else:
            rec["status"] = "wrong"
            out.append(violation(m["step"], "M6", f"{metric} claimed {claimed:+.0f}%, {pair_txt}, {raw_txt}",
                                 certain=True))   # a claim below −100% is the most certain non-reproduction there is
        statuses.append(rec["status"])
    ctx.update(claim_status=statuses, claim_detail=detail)
    return out
