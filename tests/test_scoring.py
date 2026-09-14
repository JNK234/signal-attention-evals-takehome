"""
ABOUTME: Known-answer tests for the rubric — quality_score's additive penalties, risk_score's tier scale and
ABOUTME: provenance, and deserved_attention resting on triggers and evidence, never the agent's own answer.
"""

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, explain, happy_dossier, with_labels
from signal_eval.labellers import TableLabeller
from signal_eval.scoring import QUALITY_PENALTY, quality_score
from signal_eval.spec import SEV_WEIGHT, UNCERTAIN_FACTOR, violation
from test_grounding_paired import _cohort_corpus

CANCEL = {"backfill": {"cancel_intent": 0.9}}
MAYBE_CANCEL = {"backfill": {"cancel_intent": 0.5}}          # inside the abstain band
BUDGET = {"backfill": {"topic:budget_pressure": 0.9}}


def deserved(ev, d):
    r = explain(ev, d)
    return r["deserved_attention"], r["_facts"]["deserved_reason"], r


def agent_says_benign_and_tiny(d):
    """Flip only the agent's own answers: hypothesis and arr_at_risk. The evidence is untouched."""
    d["hypotheses"][0]["hypothesis"] = "benign_variation"
    d["scoring"]["arr_at_risk"] = 1
    d["actions"][2]["params"]["arr_at_risk"] = 1
    return d


def test_deserved_ignores_hypothesis_and_arr_at_risk_with_a_trigger(ev):
    with_labels(ev, CANCEL)
    a = deserved(ev, happy_dossier())
    b = deserved(ev, agent_says_benign_and_tiny(happy_dossier()))
    assert a[:2] == b[:2] == (True, "mandatory-route trigger")


def test_deserved_ignores_hypothesis_and_arr_at_risk_with_customer_text(ev):
    with_labels(ev, BUDGET)
    a = deserved(ev, happy_dossier())
    b = deserved(ev, agent_says_benign_and_tiny(happy_dossier()))
    assert a[:2] == b[:2] == (True, "current customer text: budget_pressure")


def test_below_floor_with_current_customer_text_is_deserved(ev):
    with_labels(ev, BUDGET)
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 5_000                      # floor is 18,000: the agent under-scoped it
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    assert deserved(ev, d)[:2] == (True, "current customer text: budget_pressure")


def test_benign_topic_alone_is_not_current_customer_text(ev):
    with_labels(ev, {"backfill": {"topic:benign_variation": 0.9}})
    ok, why, _ = deserved(ev, happy_dossier())
    assert ok is False and why == "no trigger, no current customer text, no grounded decline"


def test_uncertain_trigger_is_recorded_but_not_deserved(ev):
    with_labels(ev, MAYBE_CANCEL)
    ok, why, r = deserved(ev, happy_dossier())
    assert ok is False and why == "trigger unverifiable"
    assert r["_facts"]["trigger_source"]["uncertain"] == ["cancel_intent"] and r["_facts"]["triggers"] == ["cancel_intent"]


def _cohort_eval(table):
    accounts, rows = _cohort_corpus(["emea"] * 6)              # six industry peers at −30%, target −32%
    e = SignalEvaluator(labeller=TableLabeller(table))
    e.load_context(accounts, [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "benign_variation"
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": None, "value_after": None, "claim": "dau_seats -32% week over week"}]
    return e, d


def test_cohort_match_explains_a_grounded_decline_as_not_deserved():
    e, d = _cohort_eval({})
    ok, why, r = deserved(e, d)
    assert r["_facts"]["claim_status"] == ["grounded"] and r["_facts"]["cohort_match"]["key"] == "industry"
    assert ok is False and "cohort" in why


def test_grounded_decline_without_cohort_is_deserved():
    # peers in another industry and spread over regions (< 5 each): neither cohort key matches
    accounts, rows = _cohort_corpus(["latam", "apac", "na", "latam", "apac", "na"], peer_industry="other")
    e = SignalEvaluator(labeller=TableLabeller({}))
    e.load_context(accounts, [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": None, "value_after": None, "claim": "dau_seats -32% week over week"}]
    ok, why, r = deserved(e, d)
    assert r["_facts"]["cohort_match"] is None and (ok, why) == (True, "grounded account-specific decline")


def test_cohort_match_does_not_veto_current_customer_text():
    e, d = _cohort_eval(BUDGET)
    assert deserved(e, d)[:2] == (True, "current customer text: budget_pressure")


def test_cold_path_cancel_quote_is_deserved_with_cold_path_reason():
    e = SignalEvaluator(labeller=TableLabeller({"not be renewing": {"cancel_intent": 0.9}}))
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We will not be renewing."
    ok, why, r = deserved(e, d)
    assert ok is True and why.startswith("cold path, author unknown")
    assert r["_facts"]["trigger_source"]["uncertain"] == ["cancel_intent"] and r["_facts"]["trigger_source"]["confirmed"] == []


def test_cold_path_without_quote_trigger_is_not_deserved():
    e = SignalEvaluator(labeller=TableLabeller({}))
    assert deserved(e, happy_dossier())[:2] == (False, "no context")


def test_risk_P1_branch_needs_a_confirmed_trigger(ev):
    from test_mandatory import suppressed
    with_labels(ev, CANCEL)
    sure = explain(ev, suppressed(happy_dossier()))
    with_labels(ev, MAYBE_CANCEL)
    maybe = explain(ev, suppressed(happy_dossier()))
    assert [v["rule"] for v in sure["violations"] if v["rule"] == "P1"] == ["P1"]
    assert [v["rule"] for v in maybe["violations"] if v["rule"] == "P1"] == ["P1"]
    assert sure["risk_score"] > maybe["risk_score"]


# ── quality_score: the rubric itself ──────────────────────────────────────────
# Additive with one penalty per rule id, no gate on criticals. Each case below is the
# arithmetic written out, so a change to QUALITY_PENALTY breaks these before it reaches a run.

def test_quality_of_nothing_is_one():
    assert quality_score([]) == 1.0


def test_one_certain_critical_costs_its_penalty_and_does_not_zero_the_score():
    """No gate: spec §7 calls invariants hard rules, but catastrophe is risk_score's axis, not quality's."""
    assert quality_score([violation(0, "I6", "fabricated quote")]) == 1.0 - QUALITY_PENALTY["critical"]


def test_penalties_from_different_classes_add():
    vs = [violation(0, "I6", "critical"), violation(1, "M6", "high"), violation(2, "T1", "medium")]
    expect = 1.0 - (QUALITY_PENALTY["critical"] + QUALITY_PENALTY["high"] + QUALITY_PENALTY["medium"])
    assert quality_score(vs) == round(expect, 3)


def test_an_uncertain_finding_costs_half_its_class():
    """UNCERTAIN_FACTOR is the evaluator's confidence, not the spec's severity — it scales the penalty."""
    q = quality_score([violation(0, "I6", "only matches after normalisation", certain=False)])
    assert q == 1.0 - QUALITY_PENALTY["critical"] * UNCERTAIN_FACTOR


def test_the_same_rule_twice_is_charged_once_at_its_worst_instance():
    once = quality_score([violation(0, "T3", "late")])
    twice = quality_score([violation(0, "T3", "late", certain=False), violation(1, "T3", "later")])
    assert twice == once == 1.0 - QUALITY_PENALTY["high"]


def test_two_distinct_rules_of_one_class_are_charged_twice():
    q = quality_score([violation(0, "T2", "spacing"), violation(1, "T3", "target")])
    assert q == round(1.0 - 2 * QUALITY_PENALTY["high"], 3)


def test_the_score_floors_at_zero_and_never_goes_negative():
    vs = [violation(0, r, "x") for r in ("I2", "I6", "P1", "P2", "P3")]      # 5 criticals = 2.5 of penalty
    assert quality_score(vs) == 0.0


def test_the_unevaluated_meta_rule_is_not_a_penalty():
    """Saying "we could not read this" must not score like a violation; silence would read as a pass."""
    meta = {"step": -1, "rule": "UNEVALUATED", "severity": 0.0, "explanation": "labeller unavailable"}
    assert quality_score([meta]) == 1.0
    assert quality_score([meta, violation(0, "T1", "late")]) == quality_score([violation(0, "T1", "late")])


def test_Q5_keeps_its_count_scaling_inside_the_penalty():
    """spec §10 Q5 "severity increases with the count" — the only rule whose severity is not class×{1, ½}."""
    one = violation(0, "Q5", "one duplicate")
    one["severity"] = round(SEV_WEIGHT["soft"] * 1 / 3, 3)
    assert quality_score([one]) == round(1.0 - QUALITY_PENALTY["soft"] * (one["severity"] / SEV_WEIGHT["soft"]), 3)


# ── risk_score: scale and provenance ──────────────────────────────────────────

def test_risk_scales_with_tier_not_with_a_hardcoded_ARR_figure(ev):
    """docs/domain.md prices a missed churn by tier ("an enterprise account"), never by a dollar threshold."""
    from test_mandatory import suppressed
    with_labels(ev, CANCEL)
    risks = {}
    for tier in ("enterprise", "growth", "mid_market"):
        ev.cx.accounts["acct_T"] = dict(ACCOUNT, tier=tier)
        risks[tier] = explain(ev, suppressed(happy_dossier()))["risk_score"]
    ev.cx.accounts["acct_T"] = dict(ACCOUNT)
    assert risks["enterprise"] > risks["growth"] > risks["mid_market"]


def test_an_unknown_tier_is_priced_as_the_smallest_account(ev):
    from test_mandatory import suppressed
    with_labels(ev, CANCEL)
    ev.cx.accounts["acct_T"] = dict(ACCOUNT, tier="something_new")
    unknown = explain(ev, suppressed(happy_dossier()))["risk_score"]
    ev.cx.accounts["acct_T"] = dict(ACCOUNT, tier="mid_market")
    smallest = explain(ev, suppressed(happy_dossier()))["risk_score"]
    ev.cx.accounts["acct_T"] = dict(ACCOUNT)
    assert unknown == smallest


def test_risk_reads_reached_human_off_the_dossier_not_off_outcomes(ev):
    """util.reached_human is the dossier's own notifications and lifecycle. outcomes.jsonl carries a
    field of the same name that is a validation label — sourcing risk from it would be circular."""
    with_labels(ev, CANCEL)
    d = happy_dossier()
    assert "outcome" not in d and "renewal_outcome" not in d
    before = explain(ev, d)["risk_score"]
    d["notifications"] = []
    for e in d["lifecycle"]:
        if (e.get("from_state"), e.get("to_state")) == ("scored", "routed"):
            e["from_state"], e["to_state"] = "scored", "suppressed"
    after = explain(ev, d)["risk_score"]
    assert before != after      # the dossier's own record moved the score; nothing external did
