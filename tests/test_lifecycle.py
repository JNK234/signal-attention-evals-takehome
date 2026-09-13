"""
ABOUTME: Known-answer tests for the lifecycle checks (spec §4 transitions, §7 I2/I3/I5) — one positive
ABOUTME: and one adjacent-negative per rule, asserting rule id, step and the named offender in the explanation.
"""

import pytest

from conftest import happy_dossier, rules


def hits(result, rule):
    return [v for v in result["violations"] if v["rule"] == rule]


def _shift_steps(d, start, by=1):
    """Renumber lifecycle / actions / notifications at or after `start` so the dossier stays coherent."""
    for e in d["lifecycle"]:
        if e["step"] >= start:
            e["step"] += by
    for a in d["actions"]:
        if a["step"] >= start:
            a["step"] += by
    for n in d["notifications"]:
        if n["step"] >= start:
            n["step"] += by


# ── 1. self-transitions must respect continuity (spec §4.2 + §7 I3) ─────────────────────────

def test_self_transition_in_a_state_the_machine_is_not_in_is_I3(ev):
    d = happy_dossier()
    _shift_steps(d, 2)
    # machine is in `corroborating` after step 1, but the entry claims to stay in `scored`
    d["lifecycle"].insert(2, {"step": 2, "from_state": "scored", "to_state": "scored", "at": "2026-03-02T09:30:00Z",
                              "trigger": "agent_action", "reason": "staying"})
    v = hits(ev.evaluate(d), "I3")
    assert len(v) == 1
    assert v[0]["step"] == 2
    assert "discontinuity" in v[0]["explanation"]
    assert "scored" in v[0]["explanation"] and "corroborating" in v[0]["explanation"]


def test_self_transition_in_the_current_state_is_not_I3(ev):
    d = happy_dossier()
    _shift_steps(d, 2)
    d["lifecycle"].insert(2, {"step": 2, "from_state": "corroborating", "to_state": "corroborating", "at": "2026-03-02T09:30:00Z",
                              "trigger": "agent_action", "reason": "bot alert attached, staying"})
    assert not {"I3", "TM", "I1"} & rules(ev.evaluate(d))


# ── 2. forward edges that ride a system event must carry it (spec §4.1 Table) ──────────────

def test_evidence_received_without_enrichment_returned_is_TM(ev):
    d = happy_dossier()
    d["lifecycle"][4]["trigger"] = "agent_action"                       # evidence_pending→evidence_received
    v = hits(ev.evaluate(d), "TM")
    assert len(v) == 1
    assert v[0]["step"] == 4
    assert v[0]["severity"] == pytest.approx(0.3)                      # high (0.6) × 0.5
    assert "enrichment_returned" in v[0]["explanation"] and "agent_action" in v[0]["explanation"]


def test_acknowledged_without_owner_acknowledged_is_TM(ev):
    d = happy_dossier()
    d["lifecycle"][7]["trigger"] = "agent_action"                       # routed→acknowledged
    v = hits(ev.evaluate(d), "TM")
    assert len(v) == 1
    assert v[0]["step"] == 7
    assert v[0]["severity"] == pytest.approx(0.3)
    assert "owner_acknowledged" in v[0]["explanation"] and "agent_action" in v[0]["explanation"]


def test_forward_edges_with_their_own_trigger_are_not_TM(ev):
    d = happy_dossier()                                                 # step 4 enrichment_returned, step 7 owner_acknowledged
    assert not hits(ev.evaluate(d), "TM")
    d["lifecycle"][7]["trigger"] = "human_preempt"                      # spec §4.4: preempt reaches acknowledged from any state
    assert not hits(ev.evaluate(d), "TM")


# ── 3. exit states are final for evidence and notifications too (spec §7 I2) ───────────────

def _expired_at_step_7(d):
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z",
                          "trigger": "staleness_timeout", "reason": ""}
    d["decision"]["disposition"] = "expired"


def test_evidence_and_notification_after_exit_are_I2_one_per_entry(ev):
    d = happy_dossier()
    _expired_at_step_7(d)
    d["evidence"].append(dict(d["evidence"][0], step=8, attached_at="2026-03-21T09:00:00Z"))
    d["evidence"].append(dict(d["evidence"][0], step=9, attached_at="2026-03-22T09:00:00Z"))
    d["notifications"].append(dict(d["notifications"][0], step=10, at="2026-03-23T09:00:00Z", attempt=2))
    v = hits(ev.evaluate(d), "I2")
    assert len(v) == 3
    by_step = {x["step"]: x["explanation"] for x in v}
    assert set(by_step) == {8, 9, 10}
    assert "art_T1" in by_step[8] and "2026-03-21T09:00:00Z" in by_step[8]
    assert "art_T1" in by_step[9] and "2026-03-22T09:00:00Z" in by_step[9]
    assert "notification" in by_step[10] and "2026-03-23T09:00:00Z" in by_step[10]


def test_evidence_and_notification_before_exit_are_not_I2(ev):
    d = happy_dossier()
    _expired_at_step_7(d)
    d["evidence"].append(dict(d["evidence"][0], step=5, attached_at="2026-03-20T12:00:00Z"))   # at exit, not after
    d["notifications"].append(dict(d["notifications"][0], step=6, at="2026-03-03T14:00:00Z", attempt=2))
    assert not hits(ev.evaluate(d), "I2")


# ── 4. a timed-out signal must still reach a human (spec §4.5) ─────────────────────────────

def _timed_out(d):
    d["lifecycle"] = d["lifecycle"][:4] + [
        {"step": 4, "from_state": "evidence_pending", "to_state": "scored", "at": "2026-03-04T11:00:00Z", "trigger": "enrichment_timeout", "reason": ""},
    ]
    d["actions"] = d["actions"][:2] + [
        {"step": 4, "action": "enrichment_timeout", "at": "2026-03-04T11:00:00Z", "params": {}},
        {"step": 4, "action": "score_signal", "at": "2026-03-04T11:00:00Z", "params": {"severity": "P2", "arr_at_risk": 30_000, "confidence": "medium"}},
    ]
    d["notifications"] = []


def test_expired_after_enrichment_timeout_without_reaching_a_human_is_TM(ev):
    d = happy_dossier()
    _timed_out(d)
    d["lifecycle"].append({"step": 5, "from_state": "scored", "to_state": "expired", "at": "2026-03-18T11:00:00Z",
                           "trigger": "staleness_timeout", "reason": ""})
    d["decision"].update(disposition="expired", recommended_play="watch_only")
    v = [x for x in hits(ev.evaluate(d), "TM") if "4.5" in x["explanation"]]
    assert len(v) == 1
    assert v[0]["step"] == 5
    assert "expired" in v[0]["explanation"] and "enrichment_timeout" in v[0]["explanation"]


def test_expired_after_enrichment_timeout_that_was_routed_is_not_TM(ev):
    d = happy_dossier()
    _timed_out(d)
    d["lifecycle"] += [
        {"step": 5, "from_state": "scored", "to_state": "routed", "at": "2026-03-04T12:00:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 6, "from_state": "routed", "to_state": "expired", "at": "2026-03-18T12:00:00Z", "trigger": "staleness_timeout", "reason": ""},
    ]
    d["actions"].append({"step": 5, "action": "notify_owner", "at": "2026-03-04T12:00:00Z",
                         "params": {"channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1, "severity": "P2"}})
    d["notifications"] = [{"step": 5, "at": "2026-03-04T12:00:00Z", "channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1}]
    d["decision"].update(disposition="expired")
    assert not hits(ev.evaluate(d), "TM")


# ── 5. hypothesis must be one of the seven classes (spec §3.2 / §7 I5) ─────────────────────

def test_unknown_hypothesis_class_is_I5(ev):
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "vendor_consolidation"
    v = hits(ev.evaluate(d), "I5")
    assert len(v) == 1
    assert v[0]["step"] == 2
    assert "unknown hypothesis class vendor_consolidation" in v[0]["explanation"]


@pytest.mark.parametrize("cls", ["no_hypothesis", "champion_departure", "budget_pressure", "product_gap",
                                 "onboarding_failure", "reliability_erosion", "benign_variation"])
def test_each_of_the_seven_hypothesis_classes_is_not_I5(ev, cls):
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = cls
    assert not hits(ev.evaluate(d), "I5")
