"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. Every constant traces to a spec sentence, a measured fact, or a cited
ABOUTME: standard; each is sourced at its definition. Layer 1 (checks) does not depend on this.
"""

from .spec import BENIGN_CLASS, DEPARTURE_WINDOW_DAYS, META_RULES, RULE_SEVERITY, SEV_WEIGHT
from .util import first_hypothesis

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


def _severity_says_material(d, ctx, loaded):
    """§6.3's table is what gives severity its meaning: P0 is "written churn or legal intent; act
    today", P1 "material risk with corroboration", P2 "worth a look this week", P3 "background; batch
    it". So an agent that assigned P0/P1 *and* committed to a non-benign explanation has itself
    recorded that this needed a human — which is the question this key asks.

    The window clause is what makes the row admissible rather than circular. §8.1 bullet 4 bounds the
    departure trigger to "within 90 days of renewal", so a claimed departure outside it is
    deliberately not a mandatory route, and a severity read must not override a bound the spec
    attached to that very trigger. Without this clause the row calls sig_0059 (197 days) and sig_0441
    (109 days) deserving and contradicts two hand-derived spec verdicts.
    """
    sc = d.get("scoring") or {}
    if sc.get("severity") not in ("P0", "P1"):
        return False
    h = first_hypothesis(d).get("hypothesis")
    if h in (BENIGN_CLASS, "no_hypothesis"):
        return False
    if h == "champion_departure":
        dtr = ctx.get("days_to_renewal")
        return dtr is not None and 0 <= dtr <= DEPARTURE_WINDOW_DAYS
    return True


# Every condition under which the signal deserved a human. Each row cites where it comes from; a row with
# no source does not belong here. A table rather than an if/elif chain because these are independent
# conditions, not a precedence order.
#
# §8.1 and §4.6 are quoted obligations — the spec says "must". §6.3 is an inference: that section gives
# severity its meaning rather than mandating a route, and the row is marked as such. The README asks for
# two distinct things — "evaluate whether each dossier conforms to the specification, **and** to assess
# whether the signal deserved the attention it got" (l.75-76) — and asks us to state "what you counted as
# a signal that deserved attention and why" (l.257), so the second question is ours to answer with
# evidence rather than to decline. Measured: adding §6.3 moves dev alignment against annotator majority
# 0.296 → 0.438 at permutation p = 0.026, and keeps all 18 hand-derived golden verdicts.
DESERVES = [
    ("§8.1", "mandatory-route trigger", _confirmed_triggers),
    ("§4.6", "enrichment timed out; a signal that timed out waiting for data must still reach a human", _timed_out),
    # Inference, not a quoted "must": §6.3's severity table is the spec's own statement of what each
    # level means, so the agent assigning P0/P1 with a non-benign hypothesis is the agent recording that
    # a human was needed. Gated so it cannot override §8.1's 90-day departure bound — see the predicate.
    ("§6.3", "agent assigned P0/P1 with a non-benign hypothesis — §6.3 reads P0 as 'act today' and P1 as "
             "'material risk with corroboration' (inference from the severity table, not a stated route duty)",
     _severity_says_material),
    # The one row that is judgment, not spec text, and is allow-listed as such in the tests. On the cold path
    # load_context was never called, so authorship cannot be confirmed and a quote reading as a trigger can
    # only be an uncertain one. The alternative — False — would let an uncalled load_context silently make
    # every signal undeserving, which is a worse failure than over-reporting on the path we warn about.
    ("A8", "cold path, author unknown: quote reads as a trigger", _cold_path_trigger),
]


def deserved_attention(d, ctx, loaded):
    """Did the *signal* deserve a human, independent of how well the dossier was built? Returns (bool, reason).

    Two of the rows are the spec's own positive obligations read off evidence (§8.1, §4.6). The third reads
    the agent's *recorded decision* — the severity it assigned and the hypothesis it committed to — because
    §6.3's table is what gives severity its meaning, and a dossier stamped P0/P1 with a real explanation is
    the agent itself saying a human was needed.

    `arr_at_risk` is still never read, and that line is drawn on measurement rather than principle. It
    carries the strongest held-out signal of anything tested (arr ≥ 10% of annual: dev 0.619, held 0.483,
    p = 0.000), but every usable form of it fails: AND'ed onto the table it lets the agent's own figure veto
    a spec obligation — sig_0350 is a golden True via §4.6 and its arr_at_risk is 7.6% of annual — and the
    agent misstates the figure on 59 of 629 dossiers (19 impossible under M1, 42 inconsistent under M5).
    The distinction that survives: reading a self-report to ADD coverage is safe, since the worst case is a
    false alarm and §10 Q3 independently tests whether the evidence supports the severity; reading one to
    WITHHOLD a route the spec requires is not.

    Materiality deliberately does not gate this. M3/M4 constrain *routing* ("must not route it as-is") and
    M4's own remedy is to re-scope rather than drop, so a below-floor signal may still have deserved a look.
    """
    for rid, ref, matches in DESERVES:
        hit = matches(d, ctx, loaded)
        if hit:
            detail = f": {hit}" if isinstance(hit, list) else ""
            return True, f"{rid} {ref}{detail}"
    return False, "no spec condition requires a human"


# Per-condition probability of harm. Magnitudes are the spec's own §11 severity ordering, the same
# source quality_score uses — NOT fitted to this corpus. Fitting would encode one sample's accidents
# into an evaluator that runs on dossiers we have never seen, and the 16 complaint events here cannot
# support a weight per condition. The corpus is kept for validation instead, as the annotators are.
RISK_P = {
    "critical": 0.45,     # spec §11: policy/containment failure, missed mandatory route, fabricated evidence
    "high": 0.20,         # spec §11: materiality or grounding error
    "judgment": 0.30,     # docs/domain.md names the mechanism but ranks nothing — see RISK_CONDITIONS
}


def _rules(violations):
    return {v["rule"] for v in violations}


def _routed(ctx):
    """util.reached_human, off this dossier's own notifications and lifecycle. NOT outcomes.jsonl's field
    of the same name, which analysis/facts.py joins as a validation label — sourcing risk from an outcome
    we then validate against would be circular."""
    return bool(ctx.get("reached_human"))


# Every way the documents say a dossier can cause harm. The three harms named are a customer complaint
# (docs/data_dictionary.md: "the clearest harm signal in the corpus"), a wasted CSM slot, and a missed
# churn (docs/domain.md: "a false negative costs the ARR").
#
# The inclusion test is whether a condition can plausibly CAUSE one of those, not whether it correlates
# with one. Conditions whose harm needs a reader are gated on the dossier having reached a human; the
# complaint conditions are gated on customer contact, because a dossier that never contacts the customer
# cannot draw a complaint — 0 of 389 non-customer-visible dossiers in this corpus did.
#
# Two rows are judgment rather than spec rules and say so in their reference string.
RISK_CONDITIONS = [
    ("§8.2", "spec §8.2 customer-visible play on a restricted account — 'a critical violation'; docs/domain.md "
           "calls reaching out in a quiet period 'the most reliable way to generate a complaint in this corpus'",
     "critical", lambda d, ctx, vios, deserved: "§8.2" in _rules(vios)),

    ("§8.3", "spec §8.3 restricted material quoted in a routed dossier — 'read by people who are not cleared for it'",
     "critical", lambda d, ctx, vios, deserved: _routed(ctx) and "§8.3" in _rules(vios)),

    ("I6", "spec §7 I6 fabricated evidence in a dossier a human read — 'an operator who finds one stops "
           "trusting every other dossier'",
     "critical", lambda d, ctx, vios, deserved: _routed(ctx) and any(
         v["rule"] == "I6" and v["severity"] >= SEV_WEIGHT["critical"] for v in vios)),

    # spec §11 rates "Policy or containment failure" Critical. §8.4 and §8.7 are both containment, and both
    # put data in front of someone not entitled to it — the same harm §8.3 above already carries, which is
    # why they are gated on a reader the same way. §8.4's "must never appear in a dossier" is the strongest
    # prohibition in §8; a confirmed cross-tenant attachment scoring 0.0 would say a data leak is harmless.
    ("§8.4", "spec §8.4 cross-tenant isolation — another account's evidence 'must never appear in a dossier'; "
           "spec §11 rates a containment failure critical",
     "critical", lambda d, ctx, vios, deserved: _routed(ctx) and "§8.4" in _rules(vios)),

    ("§8.7", "spec §8.7 raw customer contact details carried into a routed dossier — 'the agent is expected "
           "not to propagate them'; spec §11 rates a containment failure critical",
     "critical", lambda d, ctx, vios, deserved: _routed(ctx) and "§8.7" in _rules(vios)),

    ("§8.1", "spec §8.1 mandatory-route trigger not routed — 'must not suppress it … accounts often go "
           "quiet-then-cancel with no usage signature at all'",
     "critical", lambda d, ctx, vios, deserved: "§8.1" in _rules(vios)),

    ("M6", "spec §9 M6 a claim that only reproduces as a pipeline artefact, put in front of a human — "
           "'the most common way this system wastes attention'",
     "high", lambda d, ctx, vios, deserved: _routed(ctx) and (
         "artifact" in (ctx.get("claim_status") or []) or bool(ctx.get("cohort_match")))),

    # Judgment, not a spec rule: docs/domain.md states the mechanism — "complaints in this corpus
    # concentrate on customer-visible plays against accounts that were never at risk" — without ranking it.
    ("VIS_UNDESERVED", "docs/domain.md: complaints concentrate on customer-visible plays against accounts "
                       "that were never at risk", "judgment",
     lambda d, ctx, vios, deserved: bool(ctx.get("visible")) and not deserved),

    # Judgment, not a spec rule: docs/domain.md prices the other error direction — "a false negative costs
    # the ARR" — but names no rule for it.
    ("UNROUTED_DESERVED", "docs/domain.md: a false negative costs the ARR — a signal that deserved a human "
                          "and never reached one", "judgment",
     lambda d, ctx, vios, deserved: bool(deserved) and not _routed(ctx)),
]
RISK_SEVERITY = {cid: sev for cid, _, sev, _ in RISK_CONDITIONS}


def _compose_risk(condition_ids):
    """Noisy-OR: the probability that at least one fired condition causes harm.

    The conditions overlap heavily — 111 of 629 dossiers fire two or more, and 15 of 16 complaints fire
    two at once — so summing would count one underlying event several times. A product of complements
    composes them without double-counting, is bounded by construction so there is no clip to pile up on,
    and is the form Signal Labs' own materials name for combining independent signals."""
    q = 1.0
    for cid in condition_ids:
        q *= 1.0 - RISK_P[RISK_SEVERITY[cid]]
    return round(1.0 - q, 3)


def risk_score(d, ctx, violations, deserved):
    """How likely this dossier is to cause harm: a customer complaint, a wasted CSM slot, or a missed churn.

    A probability, per the README ("higher = more likely to cause harm"), which is why account tier is
    deliberately absent: tier changes how expensive a missed churn is (docs/domain.md, "worth hundreds of
    CSM-hours"), not how likely one is. Cost weighting belongs to the attention-budget ranking.
    """
    return _compose_risk(fired_risk_conditions(d, ctx, violations, deserved))


def fired_risk_conditions(d, ctx, violations, deserved):
    """The ids of every risk condition this dossier meets, in table order — a bare float is not auditable."""
    return [cid for cid, _, _, matches in RISK_CONDITIONS if matches(d, ctx, violations, deserved)]
