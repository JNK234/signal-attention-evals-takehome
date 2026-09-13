"""
ABOUTME: Soft quality expectations — spec §10 Q1–Q4 plus the content half of I5.
ABOUTME: Hypothesis fit uses telemetry contradictions and, when available, the NLI topic labels.
"""

from collections import Counter
from datetime import timedelta

from ..labels import TOPIC_LABELS, decide
from ..spec import ADOPTION_FULL_FRACTION, violation
from ..util import day, first_hypothesis, mean, ts

# spec §10 Q2: "the hypothesis should match what the evidence actually shows". Every named cause needs
# support from somewhere — text that reads as that cause, or grounded telemetry in the metric family the
# cause is about. benign_variation is supported by a cohort-wide move; no_hypothesis claims nothing.
TELEMETRY_SUPPORT = {
    "reliability_erosion": {"query_p95_ms", "error_rate_pct"},
    "onboarding_failure": {"dau_seats"},
}
NEEDS_SUPPORT = {"champion_departure", "budget_pressure", "product_gap", "reliability_erosion", "onboarding_failure"}
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
    # spec §10 Q2 is about the hypothesis not matching the evidence: a p95 claim that spans the docs/domain.md
    # instrumentation step is suspect only when no verified customer-authored text backs the hypothesis.
    if hyp == "reliability_erosion" and cx.p95_step and not ctx.get("has_customer_text"):
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
        telemetry_support = bool(grounded_metrics & TELEMETRY_SUPPORT.get(hyp, set()))
        ctx["hypothesis_text_support"] = support
        # decide() under the labeller's band: an abstain (None) is not "unsupported" (WP-D revisits certainty)
        supported = decide(support, f"topic:{hyp}", next((lab.get("model_id") for lab in labs), None))
        if hyp in NEEDS_SUPPORT and supported is False and not telemetry_support:
            out.append(violation(hstep, "Q2", f"{hyp} is not supported by any verified evidence (best text support {support:.2f}, no grounded telemetry for it)"))
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
    if hyp not in ("benign_variation", None) and ctx.get("cohort_match") and not ctx.get("has_customer_text"):
        cm = ctx["cohort_match"]
        out.append(violation(hstep, "Q2", f"{hyp} on a cohort-wide move: {cm['metric']} median {cm['median_pct']:+.0f}% across "
                             f"{cm['n']} other {cm['key']}={cm['value']} accounts over the same window (calendar / cohort event)"))
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
    # spec §10 Q4: "should not re-request enrichment it already received" — a request is a re-request only
    # when it follows an enrichment_returned trigger; repeated requests with no response between are a retry.
    returned = [ts(e.get("at")) for e in d.get("lifecycle") or [] if e.get("trigger") == "enrichment_returned" and ts(e.get("at"))]
    for a in d.get("actions") or []:
        at = ts(a.get("at"))
        if a.get("action") == "request_enrichment" and at and any(r < at for r in returned):
            out.append(violation(a.get("step") if a.get("step") is not None else hstep, "Q4",
                                 f"enrichment requested again at {a['at']} after it was already returned"))
    return out
