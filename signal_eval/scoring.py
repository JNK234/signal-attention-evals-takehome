"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. PLACEHOLDER rubric: weights are initial judgment calls, to be defined
ABOUTME: from the facts table and the outcome / annotator analysis. Layer 1 (checks) does not depend on this.
"""

from .labels import TOPIC_LABELS
from .spec import META_RULES, RULE_SEVERITY, SEV_WEIGHT
from .util import num

# multiplicative hit to quality_score per rule class (one penalty per rule id, its worst instance)
QUALITY_PENALTY = {"critical": 0.45, "high": 0.2, "medium": 0.1, "soft": 0.05}


def quality_score(violations):
    worst = {}
    for v in violations:
        if v["rule"] in META_RULES:      # "not evaluated" is information, not a penalty
            continue
        worst[v["rule"]] = max(worst.get(v["rule"], 0.0), v["severity"])
    q = 1.0
    for rid, sev in worst.items():
        cls = RULE_SEVERITY[rid]
        q *= 1.0 - QUALITY_PENALTY[cls] * (sev / SEV_WEIGHT[cls])
    return round(max(0.0, min(1.0, q)), 3)


def current_customer_topic(ctx):
    """The first non-benign topic a verified, current (check_evidence drops stale), customer-authored artefact
    reads as at depth 0, or None. The reading's verdicts are the only witness — not the agent's hypothesis."""
    for a in ctx.get("verified") or []:
        if a.get("author_type") != "customer":
            continue
        verdict = ((a.get("_label") or {}).get("reading") or {}).get("verdict") or {}
        for label in TOPIC_LABELS:
            topic = label[len("topic:"):]
            if topic != "benign_variation" and verdict.get(label) is True:
                return topic
    return None


def deserved_attention(d, ctx, loaded):
    """Did the *signal* deserve a human, independent of how well the dossier was built? Ordered rules; first
    match wins; returns (bool, reason). Reads only what the evidence and telemetry show — never the agent's
    hypothesis or arr_at_risk, which would grade the agent with its own answer."""
    src = ctx.get("trigger_source") or {}
    confirmed, uncertain = set(src.get("confirmed") or ()), set(src.get("uncertain") or ())
    if confirmed:
        return True, "mandatory-route trigger"
    if not loaded:
        # A8: the dossier's own quotes are all there is; a depth-0 quote that reads as a trigger counts,
        # with the author unknown it can only be an uncertain one
        if uncertain:
            return True, f"cold path, author unknown: quote reads as {sorted(uncertain)}"
        return False, "no context"
    topic = current_customer_topic(ctx)
    if topic:
        return True, f"current customer text: {topic}"
    cohort = ctx.get("cohort_match")
    if "grounded" in ctx.get("claim_status", []):
        if not cohort:
            return True, "grounded account-specific decline"
        return False, f"grounded decline shared by the cohort ({cohort['key']}={cohort['value']}), no trigger, no current customer text"
    if uncertain:
        return False, "trigger unverifiable"
    return False, "no trigger, no current customer text, no grounded decline"


def risk_score(d, ctx, violations, deserved):
    """How likely this dossier causes harm: complaint, wasted escalation, or missed churn."""
    rules = {v["rule"] for v in violations}
    scale = min(1.0, (num(ctx["acct"].get("arr")) or 0) / 500_000)
    r = 0.0
    if "P2" in rules:
        r += 0.5
    if ctx.get("visible") and not deserved:
        r += 0.3
    if "P1" in rules and (ctx.get("trigger_source") or {}).get("confirmed"):
        r += 0.4 * max(scale, 0.5)
    elif deserved and not ctx.get("reached_human"):
        r += 0.25 * max(scale, 0.5)
    if any(v["rule"] == "I6" and v["severity"] >= 0.9 for v in violations):
        r += 0.2
    if ctx.get("reached_human") and ("artifact" in ctx.get("claim_status", []) or ctx.get("cohort_match")):
        r += 0.2
    if ctx.get("reached_human") and "P3" in rules:
        r += 0.15
    return min(1.0, round(r, 3))
