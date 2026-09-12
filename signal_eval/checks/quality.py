"""
ABOUTME: Soft quality expectations — spec §10 Q1–Q4 plus the content half of I5.
ABOUTME: Hypothesis fit uses telemetry contradictions and, when available, the NLI topic labels.
"""

from collections import Counter
from datetime import timedelta

from ..classifier import NLI_THRESHOLD, TOPIC_LABELS
from ..spec import ADOPTION_FULL_FRACTION, violation
from ..util import day, first_hypothesis, mean

TEXT_HYPOTHESES = {"champion_departure", "budget_pressure", "product_gap"}   # need text to be supported
TOPIC_NAMES = {k[len("topic:"):] for k in TOPIC_LABELS}


def _labels(items):
    return [a.get("_label") for a in items or [] if a.get("_label") and not a["_label"].get("unverifiable")]


def check_quality(d, ctx, cx):
    out = []
    h = first_hypothesis(d)
    hyp, hstep = h.get("hypothesis"), h.get("step") or 0
    sev = (d.get("scoring") or {}).get("severity")

    # Q1 — oscillation
    visits = ctx.get("visits", Counter())
    if any(v >= 3 for v in visits.values()) or ctx.get("backward", 0) >= 2:
        out.append(violation(hstep, "Q1", f"oscillation: {ctx.get('backward')} backward moves, max visits {max(visits.values()) if visits else 0}"))

    # Q2 — telemetry contradictions
    opened = day(d.get("opened_at"))
    if cx.loaded and hyp == "onboarding_failure" and opened:
        rows = cx.tel.get(d.get("account_id"), {})
        vals = [rows[k]["dau_seats"] for k in rows if opened - timedelta(days=30) <= k < opened and rows[k]["usable"] and rows[k].get("dau_seats") is not None]
        seats = ctx["acct"]["seats"]
        if vals and seats and mean(vals) >= ADOPTION_FULL_FRACTION * seats:
            out.append(violation(hstep, "Q2", f"onboarding_failure but mean dau_seats {mean(vals):.0f} ≈ {mean(vals)/seats:.0%} of {seats} contracted in prior 30d"))
    if hyp == "reliability_erosion" and cx.p95_step:
        p95 = [m for m in d.get("metrics_claimed") or [] if m.get("metric") == "query_p95_ms" and day(m.get("as_of"))]
        if p95 and all(day(m["as_of"]) - timedelta(days=2 * int(m.get("window_days") or 7)) < cx.p95_step <= day(m["as_of"]) for m in p95):
            out.append(violation(hstep, "Q2", f"reliability_erosion rests only on a p95 claim spanning the {cx.p95_step} instrumentation change"))
    if hyp == "benign_variation" and ctx.get("triggers"):
        out.append(violation(hstep, "Q2", f"benign_variation while mandatory trigger present: {sorted(ctx['triggers'])}"))

    # Q2 / I5 — model: does the evidence talk about what the agent claimed?
    labs = _labels(ctx.get("verified"))
    if labs and hyp in TOPIC_NAMES:
        support = max(lab["scores"].get(f"topic:{hyp}", 0.0) for lab in labs)
        grounded_metrics = {c["metric"] for c in ctx.get("claim_detail", []) if c.get("status") == "grounded"}
        telemetry_support = ((hyp == "reliability_erosion" and grounded_metrics & {"query_p95_ms", "error_rate_pct"})
                             or (hyp == "onboarding_failure" and "dau_seats" in grounded_metrics)
                             or (hyp == "benign_variation" and "cohort" in ctx.get("claim_status", [])))
        ctx["hypothesis_text_support"] = support
        if support < NLI_THRESHOLD and not telemetry_support and hyp in TEXT_HYPOTHESES:
            out.append(violation(hstep, "Q2", f"{hyp} is not supported by any verified evidence (best text support {support:.2f})"))
    topics = Counter(lab["topic"] for lab in labs if lab.get("topic"))
    ctx["evidence_topics"] = dict(topics)
    if topics:
        top, n = topics.most_common(1)[0]
        if hyp == "no_hypothesis":
            out.append(violation(hstep, "I5", f"no_hypothesis while evidence reads as {top} ({n} artefact(s))"))
        elif hyp != top and n >= max(1, sum(topics.values()) // 2 + 1):
            out.append(violation(hstep, "Q2", f"hypothesis {hyp} but evidence reads as {top} ({n}/{sum(topics.values())} artefacts)"))
    if hyp not in ("no_hypothesis", "benign_variation", None) and not ctx.get("verified") and ctx.get("stale"):
        out.append(violation(hstep, "Q2", f"{hyp} rests only on quoted-history / sarcastic evidence"))
    if hyp not in ("benign_variation", None) and ctx.get("cohort_notes") and not ctx.get("has_customer_text"):
        out.append(violation(hstep, "Q2", f"{hyp} on a cohort-wide move: " + ctx["cohort_notes"][0]))
    if hyp not in ("benign_variation", None) and ctx.get("claim_status") and all(s == "artifact" for s in ctx["claim_status"]) and not ctx.get("has_customer_text"):
        out.append(violation(hstep, "Q2", f"{hyp} rests only on pipeline-artefact claims"))

    # Q3 — calibration
    vs = ctx.get("verified_sources")
    if sev in ("P0", "P1") and vs is not None and cx.loaded and not vs:
        out.append(violation(hstep, "Q3", f"{sev} with no verified non-bot text evidence"))
    if sev == "P0" and not ctx.get("triggers"):
        out.append(violation(hstep, "Q3", "P0 without a written-intent / legal / security trigger"))

    # Q4 — hygiene
    for aid, n in Counter(ev.get("artifact_id") for ev in d.get("evidence") or []).items():
        if n > 1:
            out.append(violation(hstep, "Q4", f"{aid} attached {n} times"))
    nreq = sum(1 for a in d.get("actions") or [] if a.get("action") == "request_enrichment")
    if nreq > 1:
        out.append(violation(hstep, "Q4", f"enrichment requested {nreq} times"))
    return out
