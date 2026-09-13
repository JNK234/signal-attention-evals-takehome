"""
ABOUTME: Known-answer tests for the layer-1 checks using small synthetic dossiers, so every spec rule
ABOUTME: has a regression test that does not depend on the corpus. Run: python -m pytest tests -q
"""

import copy
import re
from datetime import date

import pytest

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, happy_dossier, rules, telemetry
from signal_eval.spec import SEV_WEIGHT


def only(result, rule):
    return [v for v in result["violations"] if v["rule"] == rule]


def test_happy_path_has_no_violations(ev):
    assert rules(ev.evaluate(happy_dossier())) == set()


def test_skipping_enrichment_is_a_matrix_violation_and_misplaced_action(ev):
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:3] + [
        {"step": 3, "from_state": "hypothesis_formed", "to_state": "scored", "at": "2026-03-02T11:00:00Z", "trigger": "agent_action", "reason": ""},
        {"step": 4, "from_state": "scored", "to_state": "routed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": ""},
    ]
    d["actions"] = [d["actions"][0], dict(d["actions"][2], step=3), dict(d["actions"][3], step=4)]
    d["notifications"][0]["step"] = 4
    d["decision"]["disposition"] = "routed"
    r = ev.evaluate(d)
    assert {"TM", "I4"} <= rules(r)
    assert only(r, "TM")[0]["step"] == 3 and "hypothesis_formed→scored" in only(r, "TM")[0]["explanation"]
    assert only(r, "I4")[0]["step"] == 3 and "score_signal" in only(r, "I4")[0]["explanation"]


def test_backward_at_medium_confidence_is_I1(ev):
    d = happy_dossier()
    d["lifecycle"].insert(3, {"step": 3, "from_state": "hypothesis_formed", "to_state": "corroborating", "at": "2026-03-02T10:30:00Z", "trigger": "agent_action", "reason": ""})
    for e in d["lifecycle"][4:]:
        e["step"] += 1
    d["lifecycle"][4]["from_state"] = "corroborating"
    d["lifecycle"][4]["to_state"] = "hypothesis_formed"
    d["lifecycle"].insert(5, {"step": 5, "from_state": "hypothesis_formed", "to_state": "evidence_pending", "at": "2026-03-02T11:30:00Z", "trigger": "agent_action", "reason": ""})
    for e in d["lifecycle"][6:]:
        e["step"] += 1
    r = ev.evaluate(d)
    assert "I1" in rules(r) and only(r, "I1")[0]["step"] == 3


def test_action_after_exit_is_I2(ev):
    d = happy_dossier()
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z", "trigger": "staleness_timeout", "reason": ""}
    d["actions"].append({"step": 8, "action": "attach_evidence", "at": "2026-03-21T12:00:00Z", "params": {"artifact_id": "art_T1"}})
    d["decision"]["disposition"] = "expired"
    r = ev.evaluate(d)
    assert "I2" in rules(r) and only(r, "I2")[0]["step"] == 8


def test_lifecycle_discontinuity_is_I3(ev):
    """spec §7 I3: one state at a time — an entry must start where the previous one ended. Dropping
    candidate→corroborating leaves step 2 claiming to start from corroborating while the signal is in candidate."""
    d = happy_dossier()
    d["lifecycle"].pop(1)
    r = ev.evaluate(d)
    i3 = only(r, "I3")
    assert len(i3) == 1 and i3[0]["step"] == 2 and "discontinuity" in i3[0]["explanation"]
    assert not {"TM", "I1"} & rules(r)


def test_skipped_state_with_continuity_is_TM_not_I3(ev):
    """Adjacent negative: candidate→hypothesis_formed is continuous (no I3) but not in the matrix (TM, spec §4.7)."""
    d = happy_dossier()
    d["lifecycle"].pop(1)
    d["lifecycle"][1]["from_state"] = "candidate"
    r = ev.evaluate(d)
    assert "I3" not in rules(r)
    assert only(r, "TM")[0]["step"] == 2 and "candidate→hypothesis_formed" in only(r, "TM")[0]["explanation"]


def test_self_transition_from_wrong_state_is_I3_at_that_step(ev):
    """spec §4.2 makes staying in the *current* state valid; scored→scored while the signal is in corroborating
    is a discontinuity at that step (I3), not a free teleport that only surfaces on the next entry."""
    d = happy_dossier()
    d["lifecycle"].insert(2, {"step": 2, "from_state": "scored", "to_state": "scored", "at": "2026-03-02T09:30:00Z", "trigger": "agent_action", "reason": ""})
    for e in d["lifecycle"][3:]:
        e["step"] += 1
    for a in d["actions"]:
        a["step"] += 1
    d["notifications"][0]["step"] += 1
    r = ev.evaluate(d)
    assert any(v["step"] == 2 for v in only(r, "I3"))


def test_fabricated_quote_is_I6(ev):
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We are planning a backfill of about 180M rows."
    r = ev.evaluate(d)
    assert "I6" in rules(r) and any(v["severity"] == 1.0 for v in r["violations"] if v["rule"] == "I6")
    assert only(r, "I6")[0]["step"] == 2


def test_other_accounts_artifact_is_P4(ev):
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_OTHER"
    r = ev.evaluate(d)
    assert "P4" in rules(r) and only(r, "P4")[0]["step"] == 2


def test_missing_artifact_is_I6(ev):
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_NOPE"
    r = ev.evaluate(d)
    assert "I6" in rules(r) and only(r, "I6")[0]["step"] == 2


def test_empty_hypotheses_is_I5_at_step_0(ev):
    """spec §7 I5: every signal gets exactly one hypothesis; none at all is reported at step 0."""
    d = happy_dossier()
    d["hypotheses"] = []
    r = ev.evaluate(d)
    i5 = only(r, "I5")
    assert len(i5) == 1 and i5[0]["step"] == 0 and "0 hypotheses" in i5[0]["explanation"]


def test_notification_outside_owner_window_is_T1_unless_P0(ev):
    """spec §6.1: the window is the owner's local clock, not UTC. 18:30Z is inside 08–19 UTC but 19:30 in
    Europe/Berlin (CET in early March) — a UTC-only implementation would miss it."""
    d = happy_dossier()
    d["notifications"][0]["at"] = "2026-03-02T18:30:00Z"
    r = ev.evaluate(d)
    t1 = only(r, "T1")
    assert len(t1) == 1 and t1[0]["step"] == 6 and "19:30" in t1[0]["explanation"]
    d["scoring"]["severity"] = "P0"
    assert "T1" not in rules(ev.evaluate(d))


def test_notification_inside_owner_window_but_outside_utc_is_not_T1(ev):
    """spec §6.1 mirror: 07:30Z is outside 08–19 UTC but 08:30 in Europe/Berlin — inside the owner's window."""
    d = happy_dossier()
    d["notifications"][0]["at"] = "2026-03-02T07:30:00Z"
    assert "T1" not in rules(ev.evaluate(d))


def test_wrong_channel_is_P6(ev):
    """spec §8.6: notify on the owner's declared preferred_channel."""
    d = happy_dossier()
    d["notifications"][0]["channel"] = "email"
    p6 = only(ev.evaluate(d), "P6")
    assert len(p6) == 1 and p6[0]["step"] == 6
    assert "channel email" in p6[0]["explanation"] and "slack" in p6[0]["explanation"]


def test_wrong_locale_is_P6(ev):
    """spec §8.6: notify in the owner's declared locale."""
    d = happy_dossier()
    d["notifications"][0]["locale"] = "en-US"
    p6 = only(ev.evaluate(d), "P6")
    assert len(p6) == 1 and p6[0]["step"] == 6
    assert "locale en-US" in p6[0]["explanation"] and "de-DE" in p6[0]["explanation"]


def test_slow_notification_is_T3(ev):
    d = happy_dossier()
    d["notifications"][0]["at"] = "2026-03-06T14:00:00Z"   # 102h after open, P2 target 72h
    r = ev.evaluate(d)
    assert "T3" in rules(r) and only(r, "T3")[0]["step"] == 6


def test_too_many_notifications_before_ack_is_T2(ev):
    d = happy_dossier()      # acknowledged 2026-03-03T12:00 — all four below are before that
    times = ["2026-03-02T14:00:00Z", "2026-03-02T21:00:00Z", "2026-03-03T04:00:00Z", "2026-03-03T11:00:00Z"]
    d["notifications"] = [dict(d["notifications"][0], at=t, attempt=i + 1) for i, t in enumerate(times)]
    r = ev.evaluate(d)
    assert "T2" in rules(r)
    assert any(v["step"] == 6 and "4 notifications" in v["explanation"] for v in only(r, "T2"))


def test_notifications_after_ack_do_not_count_for_T2(ev):
    d = happy_dossier()
    times = ["2026-03-02T14:00:00Z", "2026-03-04T14:00:00Z", "2026-03-05T14:00:00Z", "2026-03-06T14:00:00Z"]
    d["notifications"] = [dict(d["notifications"][0], at=t, attempt=i + 1) for i, t in enumerate(times)]
    assert "T2" not in rules(ev.evaluate(d))


def _expired(d, at):
    """routed→expired on the staleness_timeout event at `at`; the last evidence was attached 2026-03-02T09:30Z."""
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": at, "trigger": "staleness_timeout", "reason": ""}
    d["closed_at"] = at
    d["decision"]["disposition"] = "expired"
    return d


def test_expired_before_14_idle_days_is_T4(ev):
    """spec §6.4 / §4.3: expiry only after 14 days with no new evidence — 8 idle days is premature."""
    r = ev.evaluate(_expired(happy_dossier(), "2026-03-10T12:00:00Z"))
    t4 = only(r, "T4")
    assert len(t4) == 1 and t4[0]["step"] == 7
    idle = re.search(r"expired after (\d+) idle days", t4[0]["explanation"])
    assert idle and int(idle.group(1)) < 14


def test_expired_after_20_idle_days_is_not_T4(ev):
    assert "T4" not in rules(ev.evaluate(_expired(happy_dossier(), "2026-03-22T12:00:00Z")))


def test_suppressed_after_enrichment_timeout_is_TM(ev):
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:4] + [
        {"step": 4, "from_state": "evidence_pending", "to_state": "scored", "at": "2026-03-04T11:00:00Z", "trigger": "enrichment_timeout", "reason": ""},
        {"step": 5, "from_state": "scored", "to_state": "suppressed", "at": "2026-03-04T12:00:00Z", "trigger": "agent_action", "reason": "no data"},
    ]
    d["actions"] = d["actions"][:2] + [{"step": 4, "action": "enrichment_timeout", "at": "2026-03-04T11:00:00Z", "params": {}},
                                        {"step": 5, "action": "suppress", "at": "2026-03-04T12:00:00Z", "params": {}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    r = ev.evaluate(d)
    assert any(v["rule"] == "TM" and v["step"] == 5 and "4.5" in v["explanation"] for v in r["violations"])


def test_human_preempt_counts_as_reached_human(ev):
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:3] + [{"step": 3, "from_state": "hypothesis_formed", "to_state": "acknowledged", "at": "2026-03-02T11:00:00Z", "trigger": "human_preempt", "reason": ""}]
    d["actions"] = d["actions"][:1]
    d["notifications"] = []
    r = ev.evaluate(d)
    assert "TM" not in rules(r) and r["_facts"]["reached_human"] is True


def test_routed_below_floor_is_M4(ev):
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 5_000
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    r = ev.evaluate(d)
    assert "M4" in rules(r) and only(r, "M4")[0]["step"] == 5 and "5,000" in only(r, "M4")[0]["explanation"]


def test_risk_above_arr_is_M1(ev):
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = ev.evaluate(d)
    assert "M1" in rules(r) and only(r, "M1")[0]["step"] == 5


def test_routed_above_arr_is_M3_and_M1(ev):
    """spec §9 M3: a routed signal must satisfy floor ≤ arr_at_risk ≤ arr_annual; above ARR is also M1."""
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = ev.evaluate(d)
    assert {"M1", "M3"} <= rules(r) and "M4" not in rules(r)
    assert only(r, "M3")[0]["step"] == 5 and "routed" in only(r, "M3")[0]["explanation"]


def test_routed_at_exactly_arr_is_material(ev):
    """Adjacent negative: arr_at_risk == arr_annual sits on the closed bound of spec §9 M3."""
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 100_000
    d["actions"][2]["params"]["arr_at_risk"] = 100_000
    assert not {"M1", "M3", "M4"} & rules(ev.evaluate(d))


def test_floor_above_arr_is_M2(ev):
    """spec §9 M2: the materiality floor must not exceed the account's annual ARR (account record wins)."""
    orig = ev.cx.accounts["acct_T"]["materiality_floor"]
    ev.cx.accounts["acct_T"]["materiality_floor"] = 150_000
    try:
        r = ev.evaluate(happy_dossier())
    finally:
        ev.cx.accounts["acct_T"]["materiality_floor"] = orig
    m2 = only(r, "M2")
    assert len(m2) == 1 and m2[0]["step"] == 5 and "150,000" in m2[0]["explanation"]


def test_floor_equal_to_arr_is_not_M2(ev):
    """Adjacent negative: spec §9 says the floor is always ≤ arr_annual — equality is allowed."""
    orig = ev.cx.accounts["acct_T"]["materiality_floor"]
    ev.cx.accounts["acct_T"]["materiality_floor"] = 100_000
    try:
        assert "M2" not in rules(ev.evaluate(happy_dossier()))
    finally:
        ev.cx.accounts["acct_T"]["materiality_floor"] = orig


def test_twelvefold_restatement_is_M5(ev):
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 5, "metric": "arr_at_risk", "window_days": None, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $360,000 (no enrichment revision on record)"}]
    r = ev.evaluate(d)
    assert any(v["rule"] == "M5" and v["step"] == 5 and "12×" in v["explanation"] for v in r["violations"])


def test_customer_visible_play_on_legal_hold_is_P2(ev):
    d = happy_dossier()
    d["decision"].update(recommended_play="csm_checkin", customer_visible=True)
    ev.cx.accounts["acct_T"]["flags"] = ["legal_hold"]
    try:
        r = ev.evaluate(d)
        assert "P2" in rules(r) and only(r, "P2")[0]["step"] == 6
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_restricted_quote_when_routed_is_P3(ev):
    """The artefact record is authoritative for `restricted`, not the dossier's own flag."""
    d = happy_dossier()
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, restricted=True)
    try:
        r = ev.evaluate(d)
        assert "P3" in rules(r) and only(r, "P3")[0]["step"] == 2
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_dossier_restricted_flag_alone_is_not_P3_when_artifact_says_otherwise(ev):
    d = happy_dossier()
    d["evidence"][0]["restricted"] = True
    assert "P3" not in rules(ev.evaluate(d))


def test_high_confidence_single_source_is_P5(ev):
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    r = ev.evaluate(d)
    assert "P5" in rules(r) and only(r, "P5")[0]["step"] == 2


def test_email_in_routed_quote_is_P7(ev):
    d = happy_dossier()
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text="Call me. ada@example.com")
    d["evidence"][0]["quote"] = "Call me. ada@example.com"
    try:
        r = ev.evaluate(d)
        assert "P7" in rules(r) and only(r, "P7")[0]["step"] == 2
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_grounded_claim_passes_M6():
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([100] * 7, [60] * 7), [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": 700, "value_after": 420, "claim": "dau_seats -40% week over week"}]
    r = e.evaluate(d)
    assert "M6" not in rules(r) and r["_facts"]["claim_status"] == ["grounded"]


def test_legacy_double_count_claim_is_labelled_artifact():
    """api_calls halves on the migration date for a legacy account; the agent reports -50%."""
    from datetime import timedelta
    end = date(2026, 5, 24)
    rows = []
    for i in range(14):
        d = end - timedelta(days=13 - i)
        rows.append({"account_id": "acct_T", "date": d.isoformat(), "dau_seats": 50, "api_calls": 2000 if d < date(2026, 5, 18) else 1000,
                     "query_p95_ms": 500, "error_rate_pct": 0.1, "dashboards_created": 1, "data_volume_gb": 1.0,
                     "ingest_status": "ok", "ingested_at": f"{(d + timedelta(days=1)).isoformat()}T03:00:00Z"})
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "api_calls", "window_days": 7, "as_of": "2026-05-24",
                             "value_before": 14000, "value_after": 7000, "claim": "api_calls -50% week over week"}]
    r = e.evaluate(d)
    assert r["_facts"]["claim_status"] == ["artifact"]
    assert any("legacy double-count" in v["explanation"] and v["step"] == 2 for v in r["violations"] if v["rule"] == "M6")


def test_degraded_window_is_unverifiable():
    """spec §9 M6: windows whose ingest_status is not ok are excluded; a claim over such a window cannot be
    grounded. Reported as a partial M6 (scale 0.4 on a high-class rule) naming the degraded day."""
    e = SignalEvaluator(use_classifier=False)
    rows = telemetry([100] * 7, [60] * 7)
    rows[10]["ingest_status"] = "degraded"
    e.load_context([ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": 700, "value_after": 420, "claim": "dau_seats -40% week over week"}]
    r = e.evaluate(d)
    assert r["_facts"]["claim_status"] == ["unverifiable"]
    m6 = only(r, "M6")
    assert len(m6) == 1 and m6[0]["step"] == 2
    assert m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * 0.4)
    assert "degraded" in m6[0]["explanation"]


def test_duplicate_signal_is_Q5_only_while_earlier_is_open():
    e = SignalEvaluator(use_classifier=False)
    first = happy_dossier()                                   # open 03-02 08:00, closed 03-03 12:00
    second = copy.deepcopy(first)
    second["signal_id"] = "sig_T2"
    second["opened_at"] = "2026-03-03T10:00:00Z"              # while first is still open
    third = copy.deepcopy(first)
    third["signal_id"] = "sig_T3"
    third["opened_at"] = "2026-03-05T08:00:00Z"               # after first closed
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [first, second, third])
    r = e.evaluate(second)
    assert "Q5" in rules(r) and only(r, "Q5")[0]["step"] == 0 and "sig_T" in only(r, "Q5")[0]["explanation"]
    assert "Q5" not in rules(e.evaluate(first))
    assert "Q5" not in rules(e.evaluate(third))


BILL = dict(ARTIFACT, artifact_id="art_BILL", type="billing_event", source="billing_event", author_type="bot",
            text="Invoice INV-9 for $20,000 issued. Status: disputed by customer AP.")


def _billing_dossier():
    """happy_dossier opened by the billing_dispute detector with the $20,000 (20% of ARR) disputed invoice attached."""
    d = happy_dossier()
    d["detector"] = "billing_dispute"
    d["lifecycle"][0]["trigger"] = "detector:billing_dispute"
    d["evidence"] = [{"step": 2, "artifact_id": "art_BILL", "source": "billing_event", "restricted": False,
                      "attached_at": "2026-03-02T09:30:00Z", "quote": "Status: disputed by customer AP."}]
    return d


def test_billing_dispute_suppressed_is_P1():
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT, BILL], [])
    d = _billing_dossier()
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": "immaterial"}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    r = e.evaluate(d)
    assert "P1" in rules(r) and "billing_dispute" in r["_facts"]["triggers"]
    assert only(r, "P1")[0]["step"] == 6


def test_P0_without_trigger_is_Q3(ev):
    """spec §10 Q3 with §6.3: P0 means written churn or legal intent; P0 with no mandatory trigger is overconfident."""
    d = happy_dossier()
    d["scoring"]["severity"] = "P0"
    r = ev.evaluate(d)
    q3 = [v for v in only(r, "Q3") if "P0 without" in v["explanation"]]
    assert len(q3) == 1 and q3[0]["step"] == 2


def test_P0_with_billing_trigger_is_not_Q3():
    """Adjacent negative: the same P0 with a structural billing-dispute trigger (spec §8.1 bullet 5) present."""
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT, BILL], [])
    d = _billing_dossier()
    d["scoring"]["severity"] = "P0"
    r = e.evaluate(d)
    assert "billing_dispute" in r["_facts"]["triggers"]
    assert not any("P0 without" in v["explanation"] for v in only(r, "Q3"))


def test_same_artifact_attached_twice_is_Q4(ev):
    """spec §10 Q4: evidence hygiene — the agent should not attach the same artefact twice."""
    d = happy_dossier()
    d["evidence"].append(dict(d["evidence"][0], step=5, attached_at="2026-03-02T12:30:00Z"))
    r = ev.evaluate(d)
    q4 = only(r, "Q4")
    assert len(q4) == 1 and q4[0]["step"] == 2 and "art_T1 attached 2 times" in q4[0]["explanation"]


def test_two_distinct_artifacts_is_not_Q4(ev):
    d = happy_dossier()
    ev.cx.artifacts["art_T2"] = dict(ARTIFACT, artifact_id="art_T2")
    d["evidence"].append(dict(d["evidence"][0], artifact_id="art_T2", step=5, attached_at="2026-03-02T12:30:00Z"))
    try:
        assert "Q4" not in rules(ev.evaluate(d))
    finally:
        del ev.cx.artifacts["art_T2"]


def _with_backward_moves(d, n):
    """Insert n allowed (low-confidence) hypothesis_formed→corroborating→hypothesis_formed round trips after step 2."""
    d["hypotheses"][0]["confidence"] = "low"
    d["scoring"]["confidence"] = "low"
    d["actions"][2]["params"]["confidence"] = "low"
    loops = []
    for i in range(n):
        loops.append({"step": 0, "from_state": "hypothesis_formed", "to_state": "corroborating", "at": f"2026-03-02T10:{10 + 2 * i:02d}:00Z", "trigger": "agent_action", "reason": "weak hypothesis, looking again"})
        loops.append({"step": 0, "from_state": "corroborating", "to_state": "hypothesis_formed", "at": f"2026-03-02T10:{11 + 2 * i:02d}:00Z", "trigger": "agent_action", "reason": ""})
    d["lifecycle"] = d["lifecycle"][:3] + loops + d["lifecycle"][3:]
    for i, e in enumerate(d["lifecycle"]):
        e["step"] = i
    for a in d["actions"]:
        if a["step"] >= 3:
            a["step"] += 2 * n
    d["notifications"][0]["step"] += 2 * n
    return d


def test_three_visits_to_corroborating_is_Q1(ev):
    """spec §10 Q1: oscillating between states without adding evidence is a quality problem even when every
    edge is the allowed low-confidence backward transition (spec §4.6) — so no I1 alongside it."""
    r = ev.evaluate(_with_backward_moves(happy_dossier(), 2))
    q1 = only(r, "Q1")
    assert len(q1) == 1 and q1[0]["step"] == 2 and "oscillation" in q1[0]["explanation"]
    assert not {"I1", "TM", "I3"} & rules(r)


def test_single_low_confidence_backward_move_is_not_Q1(ev):
    r = ev.evaluate(_with_backward_moves(happy_dossier(), 1))
    assert not {"Q1", "I1", "TM", "I3"} & rules(r)


def _evaluator_with_dau(level):
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([level] * 7, [level] * 7), [ARTIFACT], [])
    return e


def test_onboarding_failure_on_adopted_account_is_Q2():
    """spec §10 Q2 / §3.2: onboarding_failure means adoption never started; a prior-30-day dau_seats mean of 95
    on 100 contracted seats contradicts it (classifier off — telemetry alone decides)."""
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "onboarding_failure"
    r = _evaluator_with_dau(95).evaluate(d)
    q2 = only(r, "Q2")
    assert len(q2) == 1 and q2[0]["step"] == 2 and "onboarding_failure" in q2[0]["explanation"]


def test_onboarding_failure_on_unadopted_account_is_not_Q2():
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "onboarding_failure"
    assert "Q2" not in rules(_evaluator_with_dau(20).evaluate(d))


def test_cold_call_without_context_returns_contract_keys():
    r = SignalEvaluator(use_classifier=False).evaluate(happy_dossier())
    assert set(r) >= {"quality_score", "risk_score", "deserved_attention", "violations"}
    assert r["_facts"]["context_loaded"] is False


def test_self_transition_is_valid(ev):
    d = happy_dossier()
    d["lifecycle"].insert(2, {"step": 2, "from_state": "corroborating", "to_state": "corroborating", "at": "2026-03-02T09:30:00Z", "trigger": "agent_action", "reason": "bot alert attached, staying"})
    for e in d["lifecycle"][3:]:
        e["step"] += 1
    for a in d["actions"][1:]:
        a["step"] += 1
    d["notifications"][0]["step"] += 1
    assert not {"TM", "I1", "I3"} & rules(ev.evaluate(d))


def test_expired_after_notification_still_reached_a_human(ev):
    d = happy_dossier()
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z", "trigger": "staleness_timeout", "reason": ""}
    d["decision"]["disposition"] = "expired"
    r = ev.evaluate(d)
    assert r["_facts"]["reached_human"] is True


def test_numeric_range_in_quote_is_not_a_phone_number(ev):
    d = happy_dossier()
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text="Seats went from 120 - 400 over the quarter.")
    d["evidence"][0]["quote"] = "Seats went from 120 - 400 over the quarter."
    try:
        assert "P7" not in rules(ev.evaluate(d))
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_negative_days_to_renewal_is_not_near_renewal(ev):
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "champion_departure"
    ev.cx.accounts["acct_T"]["renewal_date"] = "2026-01-01"     # already passed
    try:
        r = ev.evaluate(d)
        assert "buyer_or_champion_departure" not in r["_facts"]["triggers"]
    finally:
        ev.cx.accounts["acct_T"].pop("renewal_date", None)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(hypotheses=[]),
    lambda d: d.update(notifications=None),
    lambda d: d.update(closed_at=None),
    lambda d: d["evidence"][0].update(quote=None),
    lambda d: d["notifications"][0].update(at=None),
    lambda d: d["scoring"].update(arr_at_risk="30000"),
    lambda d: d.update(lifecycle=[]),
    lambda d: d.update(metadata={}),
    lambda d: d.update(metrics_claimed=[{"step": 2, "metric": "dau_seats", "window_days": None, "as_of": None, "claim": "dau_seats fell"}]),
])
def test_malformed_dossier_never_raises(ev, mutate):
    d = happy_dossier()
    mutate(d)
    r = ev.evaluate(d)
    assert set(r) >= {"quality_score", "risk_score", "deserved_attention", "violations"}
    assert r["_facts"]["errors"] == [], r["_facts"]["errors"]


def test_malformed_dossier_never_raises_cold(ev):
    r = SignalEvaluator(use_classifier=False).evaluate({"signal_id": "x", "account_id": "acct_T"})
    assert set(r) >= {"quality_score", "risk_score", "deserved_attention", "violations"}
    assert r["_facts"]["errors"] == [], r["_facts"]["errors"]


@pytest.mark.parametrize("text,expect_head,quoted", [
    ("Sorted, thanks. Ignore the thread below.\n\nOn 20 Sep 2025, Kenji Iyer wrote:\n> Third time this week.", "Sorted, thanks. Ignore the thread below.", True),
    ("All good now.\n\n---------- Forwarded message ----------\nSubject: Other Co churn", "All good now.", True),
    ("seat true-up done.\n\n> we are cancelling", "seat true-up done.", True),
    ("Incident review -- Crowmarsh\nAttendees: Mateo\n- Mateo: 'this is the last one we can absorb'", None, False),
    ("We have decided to consolidate onto Chartroom. Treat this thread as formal notice.", None, False),
])
def test_split_quoted_detects_structure_not_tone(text, expect_head, quoted):
    from signal_eval.util import split_quoted
    head, tail, marker = split_quoted(text)
    assert bool(tail.strip()) is quoted
    if expect_head is not None:
        assert head == expect_head
    else:
        assert head == text
