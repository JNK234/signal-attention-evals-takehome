"""
ABOUTME: Known-answer tests for scoring.deserved_attention and the P1 branch of risk_score: the judgment rests on
ABOUTME: triggers, current customer text and grounded telemetry — never on the agent's own hypothesis or arr_at_risk.
"""

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, explain, happy_dossier, with_labels
from signal_eval.labellers import TableLabeller
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
