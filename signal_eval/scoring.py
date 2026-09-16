"""
ABOUTME: Layer 2 — turns the violation list and extracted facts into quality_score, risk_score and
ABOUTME: deserved_attention. Every constant traces to a spec sentence, a measured fact, or a cited
ABOUTME: standard; each is sourced at its definition. Layer 1 (checks) does not depend on this.
"""

from .spec import BENIGN_CLASS, DEPARTURE_WINDOW_DAYS, META_RULES, RULE_SEVERITY, SEV_WEIGHT
from .util import first_hypothesis

# What one rule of each class costs quality_score. One penalty per rule id (its worst instance).
#
# The spec supplies which rules exist and which class each is in (§11: Critical / High / Medium–High /
# Variable). It never says what a class costs on a 0–1 scale, so that one layer comes from the
# annotators: regressing each annotator's quality_score on the severities they themselves recorded
# gives quality ≈ intercept − slope × Σ(severity), slopes −0.161 / −0.162 / −0.145, R² .60 / .82 / .70,
# intercepts 0.84 / 0.92 / 0.99. Three independent raters agree on the slope to within 0.017 and
# disagree on the intercept by 0.15 — so the slope is the shared fact and the intercepts are where
# they differ, and only the slope is used. A severity-1.0 finding costs about 0.16.
#
# Penalty = QUALITY_SLOPE × SEV_WEIGHT[class], so the class ratios are the spec's own severity
# weights (10 : 6 : 3 : 1) and there is no third set of numbers between the spec and the score.
# The previous table, 0.50 / 0.20 / 0.10 / 0.05, cited this regression for its ratios and then set
# the critical at 3.1× the slope with no source; it sat every score 0.29 below every annotator.
#
# No gate on criticals, despite spec §7 calling invariants "hard rules". Zeroing quality on a
# critical would conflate two axes the spec keeps apart: quality_score is how well the dossier
# was *built*, risk_score is how much harm it can cause. A fabricated quote is catastrophic harm
# and lands near 1.0 risk; it should not also erase the difference between a dossier that only
# fabricated and one that fabricated and breached containment. The annotators agree — their
# minimum quality scores are 0.30 / 0.27 / 0.88, and none of the three ever recorded a 0.
QUALITY_SLOPE = 0.16
QUALITY_PENALTY = {cls: round(QUALITY_SLOPE * w, 3) for cls, w in SEV_WEIGHT.items()}


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


# Per-condition probability of harm. The spec's §11 table supplies the ORDERING only — it rates each
# kind of failure Critical / High / Medium-High / Variable and gives no numbers. The magnitudes below
# are calibrated by analogy to CVSS v3.1, which scores impact with this same noisy-OR form
# (1 − (1−C)(1−I)(1−A), §7.4) and assigns High 0.56 / Low 0.22 — a 2.55× step between adjacent levels.
# Ours is 0.45 / 0.20, a 2.25× step, inside that precedent. They are NOT fitted to this corpus: fitting
# would encode one sample's accidents into an evaluator that runs on dossiers we have never seen, and
# the 16 complaint events here cannot support a weight per condition. The corpus validates, as the
# annotators do.
#
# `judgment` sits BELOW `high`, and that ordering is the sourced part. These two rows are inferred from
# docs/domain.md rather than rated by the spec, which is indirectness in GRADE's sense — evidence that
# does not directly address the question. GRADE treats indirectness only as a reason to rate certainty
# DOWN (one to three levels) and offers no path by which indirect evidence outranks direct evidence.
# So a condition the spec never rates cannot outweigh one the spec explicitly calls High. It previously
# sat at 0.30, above `high`, which no framework surveyed supports.
#
# Honest limit: no standard derives severity-tier constants from theory. CVSS fitted its own to expert
# orderings; IEC 31010 §B.8.6 says outright that "the choice of the ordinal scale used is, to some
# extent, arbitrary" and that the remedy is validating the index against known cases, not better
# constants. That validation is what analysis/ does.
RISK_P = {
    "critical": 0.45,     # spec §11: policy/containment failure, missed mandatory route, fabricated evidence
    "high": 0.20,         # spec §11: materiality or grounding error
    "judgment": 0.15,     # inferred from docs/domain.md, never rated by the spec — GRADE indirectness
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
     "critical", lambda d, ctx, vios, deserved: _routed(ctx) and "I6" in _rules(vios)),

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

    # spec §9 M4 "Below the floor, re-scope or suppress ... It must not route it as-is", and §9 defines the
    # floor as "the minimum exposure that justifies spending a human's time on this account". So routing
    # below it spends a slot the spec says was not justified — a wasted slot by the spec's own definition,
    # which is the harm docs/domain.md prices as "a false positive costs a CSM slot". Needs no reader gate:
    # the M4 check already requires the scored→routed edge, which coincides with reached_human on all 95
    # dossiers that carry it. §11 rates a materiality error High.
    ("M4", "spec §9 M4 routed below the materiality floor — the spec's own 'minimum exposure that "
           "justifies spending a human's time'; it 'must not route it as-is'",
     "high", lambda d, ctx, vios, deserved: "M4" in _rules(vios)),

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


# Conditions that are two readings of ONE underlying event. Noisy-OR assumes the conditions are
# independent causes; where they are not, compounding charges the same event twice. §8.1 fires when a
# mandatory-route trigger was not routed, and UNROUTED_DESERVED when a signal that deserved a human never
# reached one — on a §8.1 dossier those are the same missed intervention, and they co-occur 5.0× more
# often than independence predicts. A group contributes its highest member's probability once (0.45 here)
# rather than compounding to 0.615.
#
# Grouped on the mechanism, not on the correlation: I6 and §8.7 co-occur 10.2× chance but are different
# harms — a fabricated quote destroys trust in the dossier, propagated contact details are a containment
# breach — so they are deliberately NOT grouped. Co-occurrence flags a candidate; only a shared mechanism
# justifies the grouping.
RISK_GROUPS = [{"§8.1", "UNROUTED_DESERVED"}]


def _certainty(cid, violations):
    """How sure the evaluator is of the finding behind a condition, as a factor on its probability.

    Checks mark a finding they cannot verify with `certain=False`, which halves its severity
    (spec.UNCERTAIN_FACTOR) — quality_score already honours that. Risk must too, or an unconfirmed
    mandatory-route trigger is charged the same 0.45 as a confirmed one; 35 of the 62 §8.1 findings in
    this corpus are uncertain. Conditions that read facts rather than a violation (VIS_UNDESERVED,
    UNROUTED_DESERVED) have no finding to be unsure of and count in full. Where a rule fired more than
    once, the most certain instance sets the factor."""
    cls = RULE_SEVERITY.get(cid)
    if cls is None:
        return 1.0
    inst = [v["severity"] for v in violations if v["rule"] == cid]
    if not inst:
        return 1.0
    return max(inst) / SEV_WEIGHT[cls]


def _compose_risk(condition_ids, violations=()):
    """Noisy-OR: the probability that at least one fired condition causes harm.

    A product of complements is bounded by construction, so there is no clip for scores to pile up on,
    and it is the form Signal Labs' own materials name for combining independent signals. It is also
    CVSS v3.1's impact sub-score, 1 − (1−C)(1−I)(1−A), whose per-metric constants (High 0.56 / Low 0.22)
    set the 2.55× step this rubric's 0.45 / 0.20 follows at 2.25×.

    What it does NOT do is remove double-counting. The form assumes the conditions are independent
    causes; two duplicate conditions at 0.45 compound to 0.6975, higher than either alone. The
    conditions here are measurably not independent — 111 of 629 dossiers fire two or more, 15 of 16
    complaints fire two at once, and §8.1 with UNROUTED_DESERVED co-occur 5.0× more often than chance.
    RISK_GROUPS handles the one pair that is a single mechanism; the residual correlation between the
    rest is a known overstatement, not a solved problem.

    Each condition's probability is scaled by the certainty of its finding (see _certainty), so a
    confirmed breach outranks a suspected one on the same rule."""
    remaining = set(condition_ids)
    ps = []
    for group in RISK_GROUPS:
        hit = remaining & group
        if hit:
            ps.append(max(RISK_P[RISK_SEVERITY[c]] * _certainty(c, violations) for c in hit))
            remaining -= group
    ps.extend(RISK_P[RISK_SEVERITY[c]] * _certainty(c, violations) for c in remaining)
    q = 1.0
    for p in ps:
        q *= 1.0 - p
    return round(1.0 - q, 3)


def risk_score(d, ctx, violations, deserved):
    """How likely this dossier is to cause harm: a customer complaint, a wasted CSM slot, or a missed churn.

    A probability, per the README ("higher = more likely to cause harm"), which is why account tier is
    deliberately absent: tier changes how expensive a missed churn is (docs/domain.md, "worth hundreds of
    CSM-hours"), not how likely one is. Cost weighting belongs to the attention-budget ranking.
    """
    return _compose_risk(fired_risk_conditions(d, ctx, violations, deserved), violations)


def fired_risk_conditions(d, ctx, violations, deserved):
    """The ids of every risk condition this dossier meets, in table order — a bare float is not auditable."""
    return [cid for cid, _, _, matches in RISK_CONDITIONS if matches(d, ctx, violations, deserved)]
