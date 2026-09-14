"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. PLACEHOLDER rubric: weights are initial judgment calls, to be defined
ABOUTME: from the facts table and the outcome / annotator analysis. Layer 1 (checks) does not depend on this.
"""

from .labels import TOPIC_LABELS
from .spec import META_RULES, RULE_SEVERITY, SEV_WEIGHT

# Additive hit to quality_score per rule class, one penalty per rule id (its worst instance).
#
# Additive, not multiplicative: all three annotators score quality this way. Regressing each
# annotator's quality_score on the severities they themselves recorded gives
# quality ≈ intercept − 0.17 × Σ(severity), R² = .60 / .82 / .72 — one constant slope, three
# different intercepts (0.84 / 0.92 / 0.98). Multiplicative decay matches none of them.
#
# No gate on criticals, despite spec §7 calling invariants "hard rules". Zeroing quality on a
# critical would conflate two axes the spec keeps apart: quality_score is how well the dossier
# was *built*, risk_score is how much harm it can cause. A fabricated quote is catastrophic harm
# and lands near 1.0 risk; it should not also erase the difference between a dossier that only
# fabricated and one that fabricated and breached containment. The annotators agree — their
# minimum quality scores are 0.30 / 0.27 / 0.88, and none of the three ever recorded a 0.
QUALITY_PENALTY = {"critical": 0.50, "high": 0.20, "medium": 0.10, "soft": 0.05}


def quality_score(violations):
    worst = {}
    for v in violations:
        if v["rule"] in META_RULES:      # "not evaluated" is information, not a penalty
            continue
        worst[v["rule"]] = max(worst.get(v["rule"], 0.0), v["severity"])
    # sev / SEV_WEIGHT[cls] is 1.0 for a certain finding and UNCERTAIN_FACTOR for one the
    # evaluator could not confirm. Q5 is the exception: §10 scales it by duplicate count, so
    # its ratio is a third or two thirds. Both are intended.
    penalty = sum(QUALITY_PENALTY[RULE_SEVERITY[rid]] * (sev / SEV_WEIGHT[RULE_SEVERITY[rid]])
                  for rid, sev in worst.items())
    return round(max(0.0, min(1.0, 1.0 - penalty)), 3)


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


# What a missed risk costs, by account tier. docs/domain.md prices the two error directions
# asymmetrically and names the tier, not a sum: "A missed churn on an enterprise account is worth
# hundreds of CSM-hours." accounts.jsonl carries exactly these three tiers.
TIER_SCALE = {"enterprise": 1.0, "growth": 0.6, "mid_market": 0.4}
TIER_SCALE_DEFAULT = 0.4       # an unknown or absent tier is treated as the smallest account


def risk_score(d, ctx, violations, deserved):
    """How likely this dossier causes harm: complaint, wasted escalation, or missed churn."""
    rules = {v["rule"] for v in violations}
    scale = TIER_SCALE.get(ctx["acct"].get("tier"), TIER_SCALE_DEFAULT)
    # `reached_human` here is util.reached_human — read off this dossier's own notifications and
    # lifecycle. It is NOT outcomes.jsonl's `reached_human`, which analysis/facts.py joins
    # separately as a validation label. Never source it from outcomes: risk is validated against them.
    human = ctx.get("reached_human")
    r = 0.0
    if "P2" in rules:
        r += 0.5
    if ctx.get("visible") and not deserved:
        r += 0.3
    if "P1" in rules and (ctx.get("trigger_source") or {}).get("confirmed"):
        r += 0.4 * scale
    elif deserved and not human:
        r += 0.25 * scale
    if any(v["rule"] == "I6" and v["severity"] >= SEV_WEIGHT["critical"] for v in violations):
        r += 0.2
    if human and ("artifact" in ctx.get("claim_status", []) or ctx.get("cohort_match")):
        r += 0.2
    if human and "P3" in rules:
        r += 0.15
    return min(1.0, round(r, 3))
