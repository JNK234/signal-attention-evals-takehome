"""
ABOUTME: Known-answer tests for the rubric — quality_score's additive penalties, risk_score's tier scale and
ABOUTME: provenance, and deserved_attention resting on triggers and evidence, never the agent's own answer.
"""

import pytest
from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, explain, happy_dossier, with_labels
from signal_eval.labellers import TableLabeller
from signal_eval.scoring import (QUALITY_PENALTY, RISK_CONDITIONS, RISK_P, quality_score)

SEV = {cid: sev for cid, _, sev, _ in RISK_CONDITIONS}
from signal_eval.spec import RULE_SEVERITY, SEV_WEIGHT, UNCERTAIN_FACTOR, violation
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


def test_deserved_ignores_the_agents_own_hypothesis_and_arr_at_risk(ev):
    """§9 calls arr_at_risk "the agent's estimate"; the hypothesis is the agent's conclusion. Neither may
    change whether the SIGNAL deserved a human, or the agent would be grading itself."""
    with_labels(ev, CANCEL)
    a = deserved(ev, happy_dossier())
    b = deserved(ev, agent_says_benign_and_tiny(happy_dossier()))
    assert a[0] is b[0] is True and a[1] == b[1] and "8.1" in a[1]


def test_below_floor_does_not_veto_a_mandatory_route(ev):
    """The principle the old customer-text test carried, re-targeted to the rule the spec actually states:
    §8.1 is unconditional, and M4 governs routing ("must not route it as-is"), not deserving."""
    with_labels(ev, CANCEL)
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 5_000                      # floor is 18,000: the agent under-scoped it
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    ok, why, _ = deserved(ev, d)
    assert ok is True and "8.1" in why


def test_uncertain_trigger_is_recorded_but_not_deserved(ev):
    """§8.1 asks what is "present in the evidence". A trigger we cannot confirm is not that."""
    with_labels(ev, MAYBE_CANCEL)
    ok, why, r = deserved(ev, happy_dossier())
    assert ok is False, why
    assert r["_facts"]["trigger_source"]["uncertain"] == ["cancel_intent"] and r["_facts"]["triggers"] == ["cancel_intent"]


def test_cold_path_cancel_quote_is_deserved_with_cold_path_reason():
    e = SignalEvaluator(labeller=TableLabeller({"not be renewing": {"cancel_intent": 0.9}}))
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We will not be renewing."
    ok, why, r = deserved(e, d)
    assert ok is True and "cold path" in why
    assert r["_facts"]["trigger_source"]["uncertain"] == ["cancel_intent"] and r["_facts"]["trigger_source"]["confirmed"] == []


def test_cold_path_without_quote_trigger_is_not_deserved():
    e = SignalEvaluator(labeller=TableLabeller({}))
    assert deserved(e, happy_dossier())[0] is False


def _cohort_eval(table):
    """A target account declining −32% alongside six industry peers at −30%. Kept here because
    test_checks.py's Q2 cohort case imports it; deserved_attention no longer reads cohorts at all."""
    accounts, rows = _cohort_corpus(["emea"] * 6)
    e = SignalEvaluator(labeller=TableLabeller(table))
    e.load_context(accounts, [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "benign_variation"
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": None, "value_after": None, "claim": "dau_seats -32% week over week"}]
    return e, d


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


def test_penalties_from_different_classes_compose_as_a_product():
    vs = [violation(0, "I6", "critical"), violation(1, "M6", "high"), violation(2, "T1", "medium")]
    expect = (1 - QUALITY_PENALTY["critical"]) * (1 - QUALITY_PENALTY["high"]) * (1 - QUALITY_PENALTY["medium"])
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
    assert q == round((1 - QUALITY_PENALTY["high"]) ** 2, 3)


def test_many_criticals_approach_zero_without_reaching_it():
    """The product has no floor to pile up on: five criticals stay rankable against six."""
    five = [violation(0, r, "x") for r in ("I2", "I6", "P1", "P2", "P3")]
    six = five + [violation(1, "P4", "x")]
    assert 0.0 < quality_score(six) < quality_score(five) < 0.05


def test_the_unevaluated_meta_rule_is_not_a_penalty():
    """Saying "we could not read this" must not score like a violation; silence would read as a pass."""
    meta = {"step": -1, "rule": "UNEVALUATED", "severity": 0.0, "explanation": "labeller unavailable"}
    assert quality_score([meta]) == 1.0
    assert quality_score([meta, violation(0, "T1", "late")]) == quality_score([violation(0, "T1", "late")])


def test_every_extra_violation_strictly_lowers_the_score():
    """Monotonicity is what makes the score rankable; without it the ordering means nothing."""
    vs, last = [], 1.0
    for step, rid in enumerate(("Q1", "T1", "M6", "I6", "P2", "P3", "P4", "I2")):
        vs.append(violation(step, rid, "x"))
        q = quality_score(vs)
        assert q < last, f"adding {rid} did not lower the score ({q} vs {last})"
        last = q


def test_the_order_of_violations_does_not_change_the_score():
    vs = [violation(0, "I6", "a"), violation(1, "T3", "b"), violation(2, "Q2", "c")]
    assert quality_score(vs) == quality_score(list(reversed(vs)))


def test_no_number_of_violations_reaches_the_floor():
    """Every rule broken at once still leaves a positive, distinguishable score."""
    vs = [violation(0, rid, "x") for rid in RULE_SEVERITY]
    assert 0.0 < quality_score(vs) < 0.01


def test_Q5_keeps_its_count_scaling_inside_the_penalty():
    """spec §10 Q5 "severity increases with the count" — the only rule whose severity is not class×{1, ½}."""
    one = violation(0, "Q5", "one duplicate")
    one["severity"] = round(SEV_WEIGHT["soft"] * 1 / 3, 3)
    assert quality_score([one]) == round(1.0 - QUALITY_PENALTY["soft"] * (one["severity"] / SEV_WEIGHT["soft"]), 3)


# ── risk_score: scale and provenance ──────────────────────────────────────────

def test_tier_does_not_change_how_likely_harm_is(ev):
    """The README defines risk_score as "more likely to cause harm" — a probability. Tier changes how
    EXPENSIVE a missed churn is (docs/domain.md: "worth hundreds of CSM-hours"), not how likely. Cost
    weighting belongs to the attention-budget ranking; mixing it in here makes the number mean neither."""
    from test_mandatory import suppressed
    with_labels(ev, CANCEL)
    risks = set()
    for tier in ("enterprise", "growth", "mid_market", "something_new"):
        ev.cx.accounts["acct_T"] = dict(ACCOUNT, tier=tier)
        risks.add(explain(ev, suppressed(happy_dossier()))["risk_score"])
    ev.cx.accounts["acct_T"] = dict(ACCOUNT)
    assert len(risks) == 1, f"tier changed the risk score: {risks}"


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


# ── deserved_attention: only what the spec authorises ─────────────────────────
# The spec never says positively what deserves a human except in two places. §8.1 lists five
# mandatory-route triggers the agent "must put a human on ... and must not suppress". §4.6 says a
# signal that timed out waiting for data "must still reach a human". Everything else is a judgment
# the spec declines to make, so the evaluator declines to make it too.

TRIGGER_TEXT = {
    "cancel_intent":     ("We will not be renewing our contract.", "not be renewing"),
    "legal_reference":   ("Our counsel has reviewed this and we reserve our rights.", "counsel"),
    "security_incident": ("Customer data was exposed to an unauthorised third party.", "exposed"),
}


@pytest.mark.parametrize("label", sorted(TRIGGER_TEXT))
def test_each_mandatory_route_trigger_deserves_a_human(label, ev):
    """spec §8.1: the agent "must put a human on the signal, and must not suppress it"."""
    text, key = TRIGGER_TEXT[label]
    art = dict(ARTIFACT, text=text)
    e = SignalEvaluator(labeller=TableLabeller({key: {label: 0.9}}))
    e.load_context([ACCOUNT], [OWNER], [], [art], [])
    d = happy_dossier()
    d["evidence"][0]["quote"] = text
    ok, why, _ = deserved(e, d)
    assert ok is True and "8.1" in why, why


def test_a_mandatory_trigger_deserves_a_human_even_below_the_materiality_floor(ev):
    """§8.1 is unconditional. arr_at_risk is the agent's own estimate (spec §9), so letting it veto a
    mandatory route would let the agent excuse itself by understating its own number."""
    with_labels(ev, CANCEL)
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 5_000                      # floor is 18,000
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    ok, why, _ = deserved(ev, d)
    assert ok is True and "8.1" in why


def test_a_timed_out_signal_deserves_a_human(ev):
    """spec §4.6: "A signal that timed out waiting for data must still reach a human" — the agent may not
    suppress on the grounds that it never got the evidence it asked for."""
    from test_lifecycle import _timed_out
    d = happy_dossier()
    _timed_out(d)
    ok, why, _ = deserved(ev, d)
    assert ok is True and "4.6" in why, why


def test_a_timed_out_signal_deserves_a_human_even_below_the_floor(ev):
    from test_lifecycle import _timed_out
    d = happy_dossier()
    _timed_out(d)
    d["scoring"]["arr_at_risk"] = 5_000
    ok, why, _ = deserved(ev, d)
    assert ok is True and "4.6" in why


def test_a_grounded_decline_alone_does_not_deserve_a_human():
    """No spec sentence says a usage decline requires a human. docs/domain.md: "a usage decline is not
    automatically a risk". Without a §8.1 trigger this is a judgment the spec declines to make."""
    accounts, rows = _cohort_corpus(["latam", "apac", "na", "latam", "apac", "na"], peer_industry="other")
    e = SignalEvaluator(labeller=TableLabeller({}))
    e.load_context(accounts, [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": None, "value_after": None, "claim": "dau_seats -32% week over week"}]
    ok, why, r = deserved(e, d)
    assert r["_facts"]["claim_status"] == ["grounded"]       # the decline is real and account-specific
    assert ok is False, why                                   # and still not something the spec routes


def test_a_customer_asking_for_a_feature_does_not_deserve_a_human(ev):
    """A product_gap topic is a hypothesis class (spec §3.2), not a mandatory-route trigger (§8.1).
    Treating any non-benign customer topic as deserving fires on ordinary feature requests."""
    with_labels(ev, {"backfill": {"topic:product_gap": 0.9}})
    ok, why, _ = deserved(ev, happy_dossier())
    assert ok is False, why


def test_a_trigger_only_in_quoted_history_does_not_deserve_a_human(ev):
    """§8.1 asks what is "present in the evidence", not what the customer said months ago and quoted back."""
    art = dict(ARTIFACT, text="Thanks, all good now.\n\n> On 1 Feb 2026, Ada wrote:\n> We will not be renewing.")
    e = SignalEvaluator(labeller=TableLabeller({"not be renewing": {"cancel_intent": 0.9}}))
    e.load_context([ACCOUNT], [OWNER], [], [art], [])
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Thanks, all good now."
    ok, why, r = deserved(e, d)
    assert r["_facts"]["trigger_source"]["confirmed"] == []
    assert ok is False, why


def test_a_cancellation_reported_by_an_internal_author_does_not_deserve_a_human():
    """§8.1 bullet 1 requires "a customer-side author". An AE relaying a rumour is not that."""
    art = dict(ARTIFACT, author_type="internal", text="I hear they will not be renewing.")
    e = SignalEvaluator(labeller=TableLabeller({"not be renewing": {"cancel_intent": 0.9}}))
    e.load_context([ACCOUNT], [OWNER], [], [art], [])
    d = happy_dossier()
    d["evidence"][0]["quote"] = "I hear they will not be renewing."
    ok, why, r = deserved(e, d)
    assert r["_facts"]["trigger_source"]["confirmed"] == []
    assert ok is False, why


def test_every_deserves_row_cites_a_real_spec_section():
    """The rule that keeps us honest: a condition without a spec citation does not belong in the table.
    One row (the cold path) is judgment and is allow-listed by id."""
    import pathlib
    import re

    from signal_eval.scoring import DESERVES
    spec = (pathlib.Path(__file__).resolve().parents[1] / "spec.tex").read_text()
    JUDGMENT_ROWS = {"A8"}
    assert DESERVES, "the table must not be empty"
    for rid, ref, _ in DESERVES:
        if rid in JUDGMENT_ROWS:
            continue
        n = re.fullmatch(r"§(\d+(?:\.\d+)?)", rid)
        assert n, f"{rid!r} is neither a spec section nor an allow-listed judgment row"
        assert re.search(r"\\(sub)*section\{", spec), "spec.tex has no sections; the test is broken"
        assert ref, f"{rid} has no description"


# ── risk_score: one condition per way the docs say a dossier can cause harm ────
# The README gives one line — "0-1, higher = more likely to cause harm" — and the spec never mentions
# the field. So "harm" is whatever the docs say harm is: a customer complaint, a wasted CSM slot, or a
# missed churn. Every condition below has to be able to CAUSE one of those, not merely correlate with it.

def _risk(ev, d):
    r = explain(ev, d)
    return r["risk_score"], r["_facts"]["risk_conditions"]


def test_a_clean_dossier_carries_no_risk(ev):
    with_labels(ev, {})
    risk, fired = _risk(ev, happy_dossier())
    assert risk == 0.0 and fired == []


def test_a_customer_visible_play_on_a_restricted_account_is_the_highest_single_risk(ev):
    """spec §8.2: "A dossier on a restricted account with customer_visible: true is a critical violation."
    docs/domain.md: reaching out during a quiet period is "the most reliable way to generate a complaint"."""
    with_labels(ev, {})
    ev.cx.accounts["acct_T"] = dict(ACCOUNT, flags=["mna_quiet_period"])
    d = happy_dossier()
    d["decision"].update(customer_visible=True, recommended_play="csm_checkin")
    risk, fired = _risk(ev, d)
    ev.cx.accounts["acct_T"] = dict(ACCOUNT)
    assert "P2" in fired and risk >= RISK_P[SEV["P2"]]


def test_restricted_material_only_risks_exposure_once_a_human_can_read_it(ev):
    """spec §8.3 puts the harm in the reading: the dossier "is read by people who are not cleared for it".
    An unrouted dossier exposes nothing, so the condition must not fire on it.

    Asserted against the predicate directly, not through a dossier: check_policy already declines to raise
    P3 on an unrouted dossier, so an end-to-end test passes whether or not risk_score carries its own gate
    and would not notice the gate being deleted."""
    with_labels(ev, {})
    art = dict(ARTIFACT, artifact_id="art_R", restricted=True)
    e = SignalEvaluator(labeller=TableLabeller({}))
    e.load_context([ACCOUNT], [OWNER], [], [art], [])
    d = happy_dossier(); d["evidence"][0]["artifact_id"] = "art_R"
    assert "P3" in _risk(e, d)[1]

    fires = dict(zip((c[0] for c in RISK_CONDITIONS), (c[3] for c in RISK_CONDITIONS)))["P3"]
    vios = [violation(2, "P3", "restricted artefact quoted")]
    assert fires(d, {"reached_human": True}, vios, False) is True
    assert fires(d, {"reached_human": False}, vios, False) is False, "P3 must not risk exposure with no reader"


def test_fabricated_evidence_only_risks_trust_once_a_human_can_read_it(ev):
    """spec §7 I6: "an operator who finds one stops trusting every other dossier" — the harm needs a reader."""
    fires = dict(zip((c[0] for c in RISK_CONDITIONS), (c[3] for c in RISK_CONDITIONS)))["I6"]
    vios = [violation(2, "I6", "quote not found")]
    assert fires({}, {"reached_human": True}, vios, False) is True
    assert fires({}, {"reached_human": False}, vios, False) is False


def test_a_complaint_condition_cannot_fire_without_customer_contact():
    """0 of 389 non-customer-visible dossiers in this corpus drew a complaint, against 16 of 240 visible
    ones. Contact is the mechanism, so the condition is gated on it rather than weighted by it."""
    fires = dict(zip((c[0] for c in RISK_CONDITIONS), (c[3] for c in RISK_CONDITIONS)))["VIS_UNDESERVED"]
    assert fires({}, {"visible": True}, [], False) is True
    assert fires({}, {"visible": False}, [], False) is False
    assert fires({}, {"visible": True}, [], True) is False      # deserved: the contact was warranted


def test_a_missed_mandatory_route_is_a_risk_even_though_nobody_was_contacted(ev):
    """spec §8.1: the agent "must not suppress it ... Accounts often go quiet-then-cancel with no usage
    signature at all." The harm is inaction — it cannot produce a complaint, which is why a score built
    only from complaint data would miss it."""
    from test_mandatory import suppressed
    with_labels(ev, CANCEL)
    risk, fired = _risk(ev, suppressed(happy_dossier()))
    assert "P1" in fired and risk > 0


def test_conditions_compose_as_a_noisy_or_not_a_sum(ev):
    """Two conditions at 0.45 must give 1-(0.55)^2 = 0.6975, not 0.9. The conditions overlap heavily in the
    corpus — 15 of 16 complaints fire two at once — so adding them counts one event twice."""
    with_labels(ev, CANCEL)
    ev.cx.accounts["acct_T"] = dict(ACCOUNT, flags=["legal_hold"])
    d = happy_dossier()
    d["decision"].update(customer_visible=True, recommended_play="exec_escalation")
    d["evidence"][0]["quote"] = "We are cancelling our contract."       # fabricated: not in ARTIFACT
    risk, fired = _risk(ev, d)
    ev.cx.accounts["acct_T"] = dict(ACCOUNT)
    expect = 1.0
    for cid in fired:
        expect *= 1.0 - RISK_P[SEV[cid]]
    assert risk == round(1.0 - expect, 3) and len(fired) >= 2


def test_risk_never_reaches_one_however_many_conditions_fire(ev):
    """A bounded score with no ceiling to pile up on: every extra condition still moves it."""
    from signal_eval.scoring import _compose_risk
    ids = [c[0] for c in RISK_CONDITIONS]
    assert 0.0 < _compose_risk(ids[:-1]) < _compose_risk(ids) < 1.0


def test_the_order_conditions_fire_in_does_not_change_the_score():
    from signal_eval.scoring import _compose_risk
    ids = [c[0] for c in RISK_CONDITIONS][:3]
    assert _compose_risk(ids) == _compose_risk(list(reversed(ids)))


def test_every_risk_condition_cites_a_real_spec_section():
    """The same guard the DESERVES table carries: a condition with no citation does not belong. Two rows
    are docs/domain.md judgment rather than spec rules and are allow-listed by id."""
    import pathlib
    import re
    spec = (pathlib.Path(__file__).resolve().parents[1] / "spec.tex").read_text()
    JUDGMENT = {"VIS_UNDESERVED", "UNROUTED_DESERVED"}
    assert RISK_CONDITIONS, "the table must not be empty"
    for cid, ref, sev, _ in RISK_CONDITIONS:
        assert sev in RISK_P, f"{cid}: unknown severity {sev!r}"
        if cid in JUDGMENT:
            assert "domain.md" in ref, f"{cid} is judgment and must say where it comes from"
            continue
        m = re.search(r"§(\d+(?:\.\d+)?)", ref)
        assert m, f"{cid} cites no spec section: {ref!r}"
    assert re.search(r"\\(sub)*section\{", spec), "spec.tex has no sections; the test is broken"
