"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. Every constant traces to a spec sentence, a measured fact, or a cited
ABOUTME: standard; each is sourced at its definition. Layer 1 (checks) does not depend on this.
"""

from .spec import META_RULES, RULE_SEVERITY, SEV_WEIGHT

# What one rule of each class costs quality_score. One penalty per rule id (its worst instance).
#
# Relative sizes come from the annotators, who all score quality the same shape: regressing each
# annotator's quality_score on the severities they themselves recorded gives
# quality ≈ intercept − 0.17 × Σ(severity), R² = .60 / .82 / .72 — one constant slope, three
# different intercepts (0.84 / 0.92 / 0.98). So a violation's cost is roughly linear in its
# severity, and a critical is worth about ten soft findings.
#
# No gate on criticals, despite spec §7 calling invariants "hard rules". Zeroing quality on a
# critical would conflate two axes the spec keeps apart: quality_score is how well the dossier
# was *built*, risk_score is how much harm it can cause. A fabricated quote is catastrophic harm
# and lands near 1.0 risk; it should not also erase the difference between a dossier that only
# fabricated and one that fabricated and breached containment. The annotators agree — their
# minimum quality scores are 0.30 / 0.27 / 0.88, and none of the three ever recorded a 0.
QUALITY_PENALTY = {"critical": 0.50, "high": 0.20, "medium": 0.10, "soft": 0.05}


def quality_score(violations):
    """1.0 for a clean dossier, falling as rules break. Penalties compose as a multiplicative
    complement — Π(1 − p) — so the score is bounded below by construction and never saturates.

    The earlier form, 1 − Σp clipped at zero, put 134 of 629 dossiers (21.3%) at exactly 0.0,
    two of them with no critical violation at all. That is a floor effect by the standard
    definition: Terwee et al. 2007 (J Clin Epidemiol 60(1):34–42) calls >15% at the lowest
    possible score a floor effect, whose consequence is that those items "cannot be distinguished
    from each other". Under the product the floor holds 1 dossier (0.2%), rank order survives all
    the way down, and Spearman against the three annotators is unchanged within ±0.015.

    The form is CVSS v3.1's impact sub-score, 1 − (1−C)(1−I)(1−A), and the same algebra as the
    noisy-OR Signal Labs names for combining independent signals without overcounting.

    Caveat: a product assumes the violations are independent. Several rules firing on one root
    cause are over-penalized. Charging one penalty per rule id rather than per instance limits
    this but does not remove it.
    """
    worst = {}
    for v in violations:
        if v["rule"] in META_RULES:      # "not evaluated" is information, not a penalty
            continue
        worst[v["rule"]] = max(worst.get(v["rule"], 0.0), v["severity"])
    q = 1.0
    for rid, sev in worst.items():
        cls = RULE_SEVERITY[rid]
        # sev / SEV_WEIGHT[cls] is 1.0 for a certain finding and UNCERTAIN_FACTOR for one the
        # evaluator could not confirm. Q5 is the exception: §10 scales it by duplicate count, so
        # its ratio is a third or two thirds. Both are intended.
        q *= 1.0 - QUALITY_PENALTY[cls] * (sev / SEV_WEIGHT[cls])
    q = max(0.0, min(1.0, q))
    # Three decimals everywhere the score is legible, but a dossier breaking many rules at once
    # falls below 0.0005 and would round back onto the floor we just removed (14 simultaneous
    # rules does it; the worst dossier in this corpus breaks 10). Keep three significant figures
    # down there instead, so deep failures stay ordered against each other.
    return round(q, 3) if q >= 0.001 else float(f"{q:.3g}")


def _confirmed_triggers(d, ctx, loaded):
    return sorted((ctx.get("trigger_source") or {}).get("confirmed") or ())


def _timed_out(d, ctx, loaded):
    return bool(ctx.get("enrichment_timed_out"))


def _cold_path_trigger(d, ctx, loaded):
    return None if loaded else sorted((ctx.get("trigger_source") or {}).get("uncertain") or ())


# Every condition under which a human must see the signal. A row without a spec citation does not belong
# here: the spec never says positively what deserves attention except in these two places, and inventing a
# third — "usage fell", "a customer is unhappy" — is a judgment the spec declines to make, so we decline too.
# A table rather than an if/elif chain because these are independent conditions, not a precedence order.
DESERVES = [
    ("§8.1", "mandatory-route trigger", _confirmed_triggers),
    ("§4.6", "enrichment timed out; a signal that timed out waiting for data must still reach a human", _timed_out),
    # The one row that is judgment, not spec text, and is allow-listed as such in the tests. On the cold path
    # load_context was never called, so authorship cannot be confirmed and a quote reading as a trigger can
    # only be an uncertain one. The alternative — False — would let an uncalled load_context silently make
    # every signal undeserving, which is a worse failure than over-reporting on the path we warn about.
    ("A8", "cold path, author unknown: quote reads as a trigger", _cold_path_trigger),
]


def deserved_attention(d, ctx, loaded):
    """Did the *signal* deserve a human, independent of how well the dossier was built? Returns (bool, reason).

    Reads only the spec's own positive obligations, and only from evidence — never the agent's hypothesis,
    severity or arr_at_risk, which the spec itself calls "the agent's estimate" (§9) and which M1/M5 catch it
    getting wrong. Grading the agent with its own answer would let it excuse itself by understating a number.

    Materiality deliberately does not gate this. M3/M4 constrain *routing* ("must not route it as-is") and
    M4's own remedy is to re-scope rather than drop, so a below-floor signal may still have deserved a look.
    """
    for rid, ref, matches in DESERVES:
        hit = matches(d, ctx, loaded)
        if hit:
            detail = f": {hit}" if isinstance(hit, list) else ""
            return True, f"{rid} {ref}{detail}"
    return False, "no spec condition requires a human"


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
