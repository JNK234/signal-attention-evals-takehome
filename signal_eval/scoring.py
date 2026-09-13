"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. PLACEHOLDER rubric: weights are initial judgment calls, to be defined
ABOUTME: from the facts table and the outcome / annotator analysis. Layer 1 (checks) does not depend on this.
"""

from .spec import META_RULES, RULE_SEVERITY, SEV_WEIGHT
from .util import first_hypothesis, num

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


def deserved_attention(d, ctx, loaded):
    """Did the *signal* deserve a human, independent of how well the dossier was built?
    Ordered rules; first match wins. Returns (bool, reason)."""
    af = ctx["acct"]
    risk = num((d.get("scoring") or {}).get("arr_at_risk"))
    floor = num(af.get("floor"))
    hyp = first_hypothesis(d).get("hypothesis")
    below_floor = risk is not None and floor is not None and risk < floor
    if ctx.get("triggers"):
        return True, "mandatory-route trigger"
    if not loaded:
        if below_floor:
            return False, "below materiality floor (no context)"
        n_ev, n_cl = len(d.get("evidence") or []), len(d.get("metrics_claimed") or [])
        return (hyp != "benign_variation" and (n_ev + n_cl) > 0), "no context: evidence/claims present"
    st = ctx.get("claim_status", [])
    cust = ctx.get("has_customer_text", False)
    real_claim = "grounded" in st
    # a customer wrote something real and the agent did not call it benign:
    # the agent's arr_at_risk may simply be under-scoped, so the floor does not veto
    if cust and hyp != "benign_variation":
        return True, "customer text with non-benign hypothesis"
    if st and all(s in ("artifact", "unverifiable") for s in st) and not cust:
        return False, "claims are pipeline artefacts; no customer text"
    if below_floor:
        return False, "below materiality floor"
    if hyp == "benign_variation" and real_claim and not cust:
        return False, "real decline, explained as benign"
    if real_claim:
        return True, "grounded account-specific decline"
    return False, "no grounded claim and no customer text"


def risk_score(d, ctx, violations, deserved):
    """How likely this dossier causes harm: complaint, wasted escalation, or missed churn."""
    rules = {v["rule"] for v in violations}
    scale = min(1.0, (num(ctx["acct"].get("arr")) or 0) / 500_000)
    r = 0.0
    if "P2" in rules:
        r += 0.5
    if ctx.get("visible") and not deserved:
        r += 0.3
    if "P1" in rules:
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
