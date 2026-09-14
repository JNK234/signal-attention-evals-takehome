"""
ABOUTME: Adversarial sweep of the lifecycle rules (spec §4 Table 6, §5 Table 7, §7 I1–I5): edge combinations the
ABOUTME: spec wording implies but no existing test pins. Synthetic dossiers only; expectations derived from spec.tex.
"""

import pytest

from conftest import happy_dossier, rules
from signal_eval.spec import SEV_WEIGHT, UNCERTAIN_FACTOR


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def _renumber(d):
    for i, e in enumerate(d["lifecycle"]):
        e["step"] = i
    return d


def _suppress_at(d, keep, at, from_state, reason="immaterial", with_action=True):
    """Cut the lifecycle at `keep` entries and close with from_state→suppressed at `at`."""
    d["lifecycle"] = d["lifecycle"][:keep] + [{"step": keep, "from_state": from_state, "to_state": "suppressed",
                                              "at": at, "trigger": "agent_action", "reason": reason}]
    d["actions"] = [a for a in d["actions"] if a["step"] < keep]
    if with_action:
        d["actions"].append({"step": keep, "action": "suppress", "at": at, "params": {"reason": reason}})
    d["notifications"] = [n for n in d["notifications"] if n["step"] < keep]
    d["decision"].update(disposition="suppressed", recommended_play="watch_only", reason=reason)
    d["closed_at"] = at
    return d


# ── I1 / §4.6: backward only at low confidence, judged per move ──────────────────────────────────

def test_backward_at_low_then_at_medium_flags_only_the_second_move(ev):
    """spec §4.6: hypothesis_formed→corroborating is allowed while the hypothesis is held at low confidence and is
    I1 once it is held at medium. Two round-trips: the first under a low hypothesis (step 2), the second after a
    second hypothesis at medium (step 4). Expect exactly one I1 at the medium move (step 5, high 0.6) and one I5
    for the hypothesis count (§7 I5 'exactly one'). Q1 is also legitimate here (two backward moves) and admitted."""
    d = happy_dossier()
    d["hypotheses"] = [
        {"step": 2, "hypothesis": "budget_pressure", "confidence": "low", "evidence_refs": ["art_T1"], "rationale": ""},
        {"step": 4, "hypothesis": "budget_pressure", "confidence": "medium", "evidence_refs": ["art_T1"], "rationale": ""},
    ]
    loops = [
        {"step": 0, "from_state": "hypothesis_formed", "to_state": "corroborating", "at": "2026-03-02T10:10:00Z", "trigger": "agent_action", "reason": "weak"},
        {"step": 0, "from_state": "corroborating", "to_state": "hypothesis_formed", "at": "2026-03-02T10:20:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 0, "from_state": "hypothesis_formed", "to_state": "corroborating", "at": "2026-03-02T10:30:00Z", "trigger": "agent_action", "reason": "looking again"},
        {"step": 0, "from_state": "corroborating", "to_state": "hypothesis_formed", "at": "2026-03-02T10:40:00Z", "trigger": "agent_action", "reason": ""},
    ]
    d["lifecycle"] = d["lifecycle"][:3] + loops + d["lifecycle"][3:]
    _renumber(d)
    for a in d["actions"]:
        if a["step"] >= 3:
            a["step"] += 4
    d["notifications"][0]["step"] += 4
    d["scoring"]["confidence"] = "medium"
    r = ev.evaluate(d)
    i1 = only(r, "I1")
    assert len(i1) == 1 and i1[0]["step"] == 5 and "medium" in i1[0]["explanation"]
    assert i1[0]["severity"] == SEV_WEIGHT["critical"]
    assert any("2 hypotheses" in v["explanation"] for v in only(r, "I5"))


def test_backward_from_evidence_pending_at_low_is_allowed_and_at_medium_is_I1(ev):
    """spec §4.6: 'From hypothesis_formed or evidence_pending, the agent may go back to corroborating if the
    current hypothesis is held at low confidence.' Same edge at medium → I1."""
    def build(conf):
        d = happy_dossier()
        d["hypotheses"][0]["confidence"] = conf
        d["scoring"]["confidence"] = conf
        d["actions"][2]["params"]["confidence"] = conf
        back = [
            {"step": 0, "from_state": "evidence_pending", "to_state": "corroborating", "at": "2026-03-02T11:10:00Z", "trigger": "agent_action", "reason": "weak"},
            {"step": 0, "from_state": "corroborating", "to_state": "hypothesis_formed", "at": "2026-03-02T11:20:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 0, "from_state": "hypothesis_formed", "to_state": "evidence_pending", "at": "2026-03-02T11:30:00Z", "trigger": "agent_action", "reason": ""},
        ]
        d["lifecycle"] = d["lifecycle"][:4] + back + d["lifecycle"][4:]
        _renumber(d)
        for a in d["actions"]:
            if a["step"] >= 4:
                a["step"] += 3
        d["notifications"][0]["step"] += 3
        return d
    assert not {"I1", "TM", "I3"} & rules(ev.evaluate(build("low")))
    i1 = only(ev.evaluate(build("medium")), "I1")
    assert len(i1) == 1 and i1[0]["step"] == 4 and "evidence_pending→corroborating" in i1[0]["explanation"]


def test_backward_to_a_state_not_in_the_matrix_is_I1_even_at_low_confidence(ev):
    """spec §4.6 / §7 I1: the exception is the corroborating edge only. evidence_received→hypothesis_formed is
    backward and not in Table 6 → I1 regardless of confidence."""
    d = happy_dossier()
    d["hypotheses"][0]["confidence"] = "low"
    d["scoring"]["confidence"] = "low"
    d["actions"][2]["params"]["confidence"] = "low"
    back = [
        {"step": 0, "from_state": "evidence_received", "to_state": "hypothesis_formed", "at": "2026-03-02T12:10:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 0, "from_state": "hypothesis_formed", "to_state": "evidence_pending", "at": "2026-03-02T12:20:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 0, "from_state": "evidence_pending", "to_state": "evidence_received", "at": "2026-03-02T12:30:00Z", "trigger": "enrichment_returned", "reason": ""},
    ]
    d["lifecycle"] = d["lifecycle"][:5] + back + d["lifecycle"][5:]
    _renumber(d)
    for a in d["actions"]:
        if a["step"] >= 5:
            a["step"] += 3
    d["notifications"][0]["step"] += 3
    i1 = only(ev.evaluate(d), "I1")
    assert len(i1) == 1 and i1[0]["step"] == 5 and "evidence_received→hypothesis_formed" in i1[0]["explanation"]


# ── TM: suppression edges (Table 6 caption: ev_pend→suppressed is NOT allowed; routed/ackd rows have no Yes) ──

@pytest.mark.parametrize("keep,from_state,at", [
    (4, "evidence_pending", "2026-03-02T11:30:00Z"),
    (7, "routed", "2026-03-02T15:00:00Z"),
    (8, "acknowledged", "2026-03-03T13:00:00Z"),
])
def test_suppress_from_forbidden_state_is_TM(ev, keep, from_state, at):
    """spec §4.7 Table 6: suppressed column is '---' for ev_pend, routed and ackd. Expect one TM at the suppression
    step naming the edge, high 0.6 (certain)."""
    d = _suppress_at(happy_dossier(), keep, at, from_state)
    tm = [v for v in only(ev.evaluate(d), "TM") if "not an allowed edge" in v["explanation"]]
    assert len(tm) == 1 and tm[0]["step"] == keep and f"{from_state}→suppressed" in tm[0]["explanation"]
    assert tm[0]["severity"] == SEV_WEIGHT["high"]


@pytest.mark.parametrize("keep,from_state,at", [
    (1, "candidate", "2026-03-02T08:30:00Z"),
    (3, "hypothesis_formed", "2026-03-02T10:30:00Z"),
    (5, "evidence_received", "2026-03-02T12:30:00Z"),
])
def test_suppress_from_an_allowed_progression_state_is_not_TM(ev, keep, from_state, at):
    """spec §4.3 'From any progression state … may close to suppressed' minus the Table 6 exceptions."""
    d = _suppress_at(happy_dossier(), keep, at, from_state)
    assert not {"TM", "I1", "I3", "I4"} & rules(ev.evaluate(d))


def test_suppression_without_a_stated_reason_is_TM_uncertain_and_I4_params(ev):
    """spec §4.3 'Suppression must carry a stated reason' and Table 7 suppress 'Must carry a reason'. An empty
    reason on the edge → TM (uncertain, 0.3); a suppress action with no `reason` param → I4 ('params missing')."""
    d = _suppress_at(happy_dossier(), 6, "2026-03-02T14:00:00Z", "scored", reason="")
    d["actions"][-1]["params"] = {}
    r = ev.evaluate(d)
    tm = [v for v in only(r, "TM") if "without a stated reason" in v["explanation"]]
    assert len(tm) == 1 and tm[0]["step"] == 6 and tm[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)
    i4 = only(r, "I4")
    assert len(i4) == 1 and "reason" in i4[0]["explanation"]


def test_transition_to_suppressed_without_a_suppress_action_is_uncertain_I4(ev):
    """spec §7 I4 'suppress must always lead to the suppressed state' read the other way: a suppressed edge with
    no suppress action on it. The evaluator cannot know whether the action was dropped from the record → I4 at
    half weight (0.3) on the suppression step."""
    d = _suppress_at(happy_dossier(), 6, "2026-03-02T14:00:00Z", "scored", with_action=False)
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 6 and "without a suppress action" in i4[0]["explanation"]
    assert i4[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)


def test_second_suppress_action_after_the_exit_is_I2(ev):
    """Table 7 suppress: 'No further actions after this.' A second suppress action later than the exit edge is I2
    (§7 I2 'exit states are final'), never a clean pass."""
    d = _suppress_at(happy_dossier(), 6, "2026-03-02T14:00:00Z", "scored")
    d["actions"].append({"step": 6, "action": "suppress", "at": "2026-03-02T14:05:00Z", "params": {"reason": "again"}})
    r = ev.evaluate(d)
    i2 = only(r, "I2")
    assert len(i2) == 1 and i2[0]["step"] == 6 and "suppress" in i2[0]["explanation"]


# ── TM: expiry, timeout, preempt, opening ───────────────────────────────────────────────────────

def test_expired_from_acknowledged_on_staleness_timeout_is_allowed_with_no_T4(ev):
    """Table 6: ackd→expired is 'Yes'. §4.4/§6.4 condition expiry on 'no owner has acknowledged', so after
    acknowledgement the 14-day clock is not a condition → no TM, no T4 even 2 days later."""
    d = happy_dossier()
    d["lifecycle"].append({"step": 8, "from_state": "acknowledged", "to_state": "expired", "at": "2026-03-05T12:00:00Z",
                           "trigger": "staleness_timeout", "reason": ""})
    d["closed_at"] = "2026-03-05T12:00:00Z"
    d["decision"]["disposition"] = "expired"
    assert not {"TM", "T4", "I1", "I2", "I3"} & rules(ev.evaluate(d))


def test_expired_without_staleness_timeout_trigger_is_uncertain_TM(ev):
    """spec §3.3 / §6.4: expiry is the staleness_timeout system event. routed→expired on 'agent_action' → TM at
    half weight (the edge is allowed; the trigger is what is wrong)."""
    d = happy_dossier()
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z", "trigger": "agent_action", "reason": ""}
    d["decision"]["disposition"] = "expired"
    tm = only(ev.evaluate(d), "TM")
    assert len(tm) == 1 and tm[0]["step"] == 7 and "staleness_timeout" in tm[0]["explanation"]
    assert tm[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)


def test_evidence_pending_to_scored_without_enrichment_timeout_is_TM(ev):
    """Table 6 caption: '† at ev_pend→scored = only on an enrichment_timeout event'. The same edge on agent_action
    is TM (certain, 0.6) and the score_signal riding it is I4 ('without enrichment_timeout')."""
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:4] + [
        {"step": 4, "from_state": "evidence_pending", "to_state": "scored", "at": "2026-03-02T12:00:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 5, "from_state": "scored", "to_state": "routed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 6, "from_state": "routed", "to_state": "acknowledged", "at": "2026-03-03T12:00:00Z", "trigger": "owner_acknowledged", "reason": ""},
    ]
    d["actions"] = d["actions"][:2] + [dict(d["actions"][2], step=4, at="2026-03-02T12:00:00Z"), dict(d["actions"][3], step=5)]
    d["notifications"][0]["step"] = 5
    r = ev.evaluate(d)
    tm = only(r, "TM")
    assert len(tm) == 1 and tm[0]["step"] == 4 and "enrichment_timeout" in tm[0]["explanation"] and tm[0]["severity"] == SEV_WEIGHT["high"]
    assert any(v["step"] == 4 and "score_signal" in v["explanation"] and "enrichment_timeout" in v["explanation"] for v in only(r, "I4"))


def test_score_signal_on_the_timeout_edge_with_waited_minutes_is_not_I4(ev):
    """Table 7 read with §4.5: after enrichment_timeout the signal is in scored, so score_signal rides
    ev_pend→scored on the timeout event. Both actions on the edge, params complete → no I4, no TM."""
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:4] + [
        {"step": 4, "from_state": "evidence_pending", "to_state": "scored", "at": "2026-03-04T11:00:00Z", "trigger": "enrichment_timeout", "reason": ""},
        {"step": 5, "from_state": "scored", "to_state": "routed", "at": "2026-03-04T12:00:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 6, "from_state": "routed", "to_state": "acknowledged", "at": "2026-03-04T15:00:00Z", "trigger": "owner_acknowledged", "reason": ""},
    ]
    d["actions"] = d["actions"][:2] + [
        {"step": 4, "action": "enrichment_timeout", "at": "2026-03-04T11:00:00Z", "params": {"waited_minutes": 2880}},
        dict(d["actions"][2], step=4, at="2026-03-04T11:00:00Z"),
        dict(d["actions"][3], step=5, at="2026-03-04T12:00:00Z"),
    ]
    d["notifications"] = [dict(d["notifications"][0], step=5, at="2026-03-04T12:00:00Z")]
    d["closed_at"] = "2026-03-04T15:00:00Z"
    assert not {"I4", "TM", "I1", "I3"} & rules(ev.evaluate(d))


def test_human_preempt_from_candidate_is_allowed_but_agent_action_to_acknowledged_is_TM(ev):
    """spec §4.4: human_preempt → acknowledged from any progression state (Table 6 '†' in the ackd column).
    candidate→acknowledged on human_preempt is clean; the same edge on agent_action is TM."""
    def build(trigger):
        d = happy_dossier()
        d["lifecycle"] = d["lifecycle"][:1] + [{"step": 1, "from_state": "candidate", "to_state": "acknowledged",
                                               "at": "2026-03-02T08:30:00Z", "trigger": trigger, "reason": ""}]
        d["actions"] = []
        d["evidence"] = []
        d["notifications"] = []
        d["hypotheses"][0]["step"] = 0
        d["closed_at"] = "2026-03-02T08:30:00Z"
        return d
    assert not {"TM", "I1", "I3"} & rules(ev.evaluate(build("human_preempt")))
    tm = only(ev.evaluate(build("agent_action")), "TM")
    assert len(tm) == 1 and tm[0]["step"] == 1 and "human_preempt" in tm[0]["explanation"]


def test_opening_edge_with_a_different_detector_trigger_is_uncertain_TM(ev):
    """spec §4.1: idle→candidate 'detector fires'. The dossier says detector seat_decay but the edge is tagged
    detector:usage_cliff → TM at half weight on step 0."""
    d = happy_dossier()
    d["lifecycle"][0]["trigger"] = "detector:usage_cliff"
    tm = only(ev.evaluate(d), "TM")
    assert len(tm) == 1 and tm[0]["step"] == 0 and "usage_cliff" in tm[0]["explanation"]
    assert tm[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)


def test_transition_out_of_an_exit_state_is_I2(ev):
    """spec §7 I2: 'No transitions out of exit states are allowed.' suppressed→routed → I2 critical at that step."""
    d = _suppress_at(happy_dossier(), 6, "2026-03-02T14:00:00Z", "scored")
    d["lifecycle"].append({"step": 7, "from_state": "suppressed", "to_state": "routed", "at": "2026-03-02T15:00:00Z", "trigger": "agent_action", "reason": "reopened"})
    i2 = only(ev.evaluate(d), "I2")
    assert len(i2) == 1 and i2[0]["step"] == 7 and "suppressed→routed" in i2[0]["explanation"] and i2[0]["severity"] == SEV_WEIGHT["critical"]


# ── I3: timestamps must be strictly increasing (data dictionary) ─────────────────────────────────

def test_two_transitions_at_the_same_instant_is_uncertain_I3(ev):
    """docs/data_dictionary.md: 'transition timestamps are strictly increasing, so the state at any instant is
    unambiguous'. Two entries at 10:00:00Z share an instant → I3 at half weight on the second (step 3)."""
    d = happy_dossier()
    d["lifecycle"][3]["at"] = d["lifecycle"][2]["at"]
    d["actions"][1]["at"] = d["lifecycle"][2]["at"]
    i3 = only(ev.evaluate(d), "I3")
    assert len(i3) == 1 and i3[0]["step"] == 3 and "not after" in i3[0]["explanation"]
    assert i3[0]["severity"] == pytest.approx(SEV_WEIGHT["critical"] * UNCERTAIN_FACTOR)


# ── I4: actions placed in time (Table 7) ────────────────────────────────────────────────────────

def test_notify_before_scored_during_corroborating_is_I4(ev):
    """Table 7: notify_owner 'Only on scored→routed'. A notify at 09:45 (state corroborating) declared on step 1
    → I4 naming the last transition (candidate→corroborating). T1 does not apply (10:45 Berlin is in window)."""
    d = happy_dossier()
    d["actions"].insert(1, {"step": 1, "action": "notify_owner", "at": "2026-03-02T09:45:00Z",
                            "params": {"channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1, "severity": "P2"}})
    d["notifications"].insert(0, {"step": 1, "at": "2026-03-02T09:45:00Z", "channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1})
    d["notifications"][1]["attempt"] = 2
    d["actions"][-1]["params"]["attempt"] = 2
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 1
    assert "notify_owner" in i4[0]["explanation"] and "candidate→corroborating" in i4[0]["explanation"]


def test_attach_during_hypothesis_formed_is_I4(ev):
    """Table 7: attach_evidence 'During corroborating or evidence_received'. An attach at 10:30 (hypothesis_formed
    from 10:00 to 11:00) → I4 'during hypothesis_formed'."""
    d = happy_dossier()
    d["actions"][0].update(step=3, at="2026-03-02T10:30:00Z")
    d["evidence"][0].update(step=3, attached_at="2026-03-02T10:30:00Z")
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 3 and "attach_evidence" in i4[0]["explanation"] and "during hypothesis_formed" in i4[0]["explanation"]


def test_attach_before_the_signal_opened_is_I4(ev):
    """Table 7: an attach at 07:00, before idle→candidate at 08:00, happens in no state at all → I4."""
    d = happy_dossier()
    d["actions"][0].update(step=0, at="2026-03-02T07:00:00Z")
    d["evidence"][0].update(step=0, attached_at="2026-03-02T07:00:00Z")
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 0 and "before the first transition" in i4[0]["explanation"]


def test_action_with_extra_params_is_not_I4(ev):
    """Table 7 names what each action must carry; extra keys are not a defect (the evaluator tolerates them)."""
    d = happy_dossier()
    d["actions"][2]["params"]["note"] = "scored on enrichment payload"
    d["actions"][3]["params"]["template"] = "v2"
    assert "I4" not in rules(ev.evaluate(d))


def test_action_without_a_timestamp_is_I4(ev):
    """Table 7 places actions on transitions; an action with no `at` cannot be placed → I4 naming that."""
    d = happy_dossier()
    d["actions"][2]["at"] = None
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 5 and "timestamp" in i4[0]["explanation"]


def test_request_enrichment_on_the_wrong_edge_is_I4(ev):
    """Table 7 / §7 I4: request_enrichment 'must only happen from hypothesis_formed'. Riding candidate→corroborating
    (09:00) → I4 naming the edge it rode."""
    d = happy_dossier()
    d["actions"][1].update(step=1, at="2026-03-02T09:00:00Z")
    i4 = only(ev.evaluate(d), "I4")
    assert len(i4) == 1 and i4[0]["step"] == 1 and "request_enrichment" in i4[0]["explanation"] and "candidate→corroborating" in i4[0]["explanation"]


# ── I5: exactly one hypothesis ──────────────────────────────────────────────────────────────────

def test_two_hypotheses_is_I5_with_the_count(ev):
    """spec §7 I5 'exactly one of the seven hypothesis classes'. Two entries (both valid classes) → one I5,
    medium 0.3, reported at the first hypothesis step, saying '2 hypotheses'."""
    d = happy_dossier()
    d["hypotheses"].append({"step": 4, "hypothesis": "product_gap", "confidence": "low", "evidence_refs": [], "rationale": ""})
    i5 = only(ev.evaluate(d), "I5")
    assert len(i5) == 1 and i5[0]["step"] == 2 and "2 hypotheses" in i5[0]["explanation"] and i5[0]["severity"] == SEV_WEIGHT["critical"]


def test_hypothesis_with_none_class_is_I5(ev):
    """A hypothesis entry whose class is null is not one of the seven → I5 ('unknown hypothesis class')."""
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = None
    i5 = only(ev.evaluate(d), "I5")
    assert len(i5) == 1 and "unknown hypothesis class" in i5[0]["explanation"]


# ── snapshot semantics: a dossier still in flight ───────────────────────────────────────────────

def test_still_open_dossier_inside_its_TTA_has_no_lifecycle_or_timing_findings(ev):
    """A dossier cut at corroborating, opened 2h ago, closed_at null: nothing in §4–§6 has been breached yet
    (T3 is measured to the first notify and no routing happened; T4 needs 14 idle days). Expect none of
    I1/I2/I3/TM/I4/T1–T4."""
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:2]
    d["actions"] = d["actions"][:1]
    d["actions"][0]["step"] = 1                # the attach happens during the stay transition 1 opened (A6)
    d["notifications"] = []
    d["closed_at"] = None
    d["hypotheses"] = []
    d["scoring"] = {}
    d["decision"] = {"disposition": None, "recommended_play": None, "customer_visible": False, "reason": None}
    r = ev.evaluate(d)
    assert not {"I1", "I2", "I3", "TM", "I4", "T1", "T2", "T3", "T4"} & rules(r)
