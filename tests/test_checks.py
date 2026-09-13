"""
ABOUTME: Known-answer tests for the layer-1 checks using small synthetic dossiers, so every spec rule
ABOUTME: has a regression test that does not depend on the corpus. Run: python -m pytest tests -q
"""

import copy
from datetime import date

import pytest

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, happy_dossier, rules, telemetry

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
    assert {"TM", "I4"} <= rules(ev.evaluate(d))


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
    assert "I1" in rules(ev.evaluate(d))


def test_action_after_exit_is_I2(ev):
    d = happy_dossier()
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z", "trigger": "staleness_timeout", "reason": ""}
    d["actions"].append({"step": 8, "action": "attach_evidence", "at": "2026-03-21T12:00:00Z", "params": {"artifact_id": "art_T1"}})
    d["decision"]["disposition"] = "expired"
    assert "I2" in rules(ev.evaluate(d))


def test_fabricated_quote_is_I6(ev):
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We are planning a backfill of about 180M rows."
    r = ev.evaluate(d)
    assert "I6" in rules(r) and any(v["severity"] == 1.0 for v in r["violations"] if v["rule"] == "I6")


def test_other_accounts_artifact_is_P4(ev):
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_OTHER"
    assert "P4" in rules(ev.evaluate(d))


def test_missing_artifact_is_I6(ev):
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_NOPE"
    assert "I6" in rules(ev.evaluate(d))


def test_notification_outside_owner_window_is_T1_unless_P0(ev):
    d = happy_dossier()
    d["notifications"][0]["at"] = "2026-03-02T01:00:00Z"   # 02:00 Berlin
    assert "T1" in rules(ev.evaluate(d))
    d["scoring"]["severity"] = "P0"
    assert "T1" not in rules(ev.evaluate(d))


def test_wrong_channel_and_locale_is_P6(ev):
    d = happy_dossier()
    d["notifications"][0].update(channel="email", locale="en-US")
    assert "P6" in rules(ev.evaluate(d))


def test_slow_notification_is_T3(ev):
    d = happy_dossier()
    d["notifications"][0]["at"] = "2026-03-06T14:00:00Z"   # 102h after open, P2 target 72h
    assert "T3" in rules(ev.evaluate(d))


def test_too_many_notifications_before_ack_is_T2(ev):
    d = happy_dossier()      # acknowledged 2026-03-03T12:00 — all four below are before that
    times = ["2026-03-02T14:00:00Z", "2026-03-02T21:00:00Z", "2026-03-03T04:00:00Z", "2026-03-03T11:00:00Z"]
    d["notifications"] = [dict(d["notifications"][0], at=t, attempt=i + 1) for i, t in enumerate(times)]
    assert "T2" in rules(ev.evaluate(d))


def test_notifications_after_ack_do_not_count_for_T2(ev):
    d = happy_dossier()
    times = ["2026-03-02T14:00:00Z", "2026-03-04T14:00:00Z", "2026-03-05T14:00:00Z", "2026-03-06T14:00:00Z"]
    d["notifications"] = [dict(d["notifications"][0], at=t, attempt=i + 1) for i, t in enumerate(times)]
    assert "T2" not in rules(ev.evaluate(d))


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
    assert any(v["rule"] == "TM" and "4.5" in v["explanation"] for v in r["violations"])


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
    assert "M4" in rules(ev.evaluate(d))


def test_risk_above_arr_is_M1(ev):
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    assert "M1" in rules(ev.evaluate(d))


def test_twelvefold_restatement_is_M5(ev):
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 5, "metric": "arr_at_risk", "window_days": None, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $360,000 (no enrichment revision on record)"}]
    r = ev.evaluate(d)
    assert any(v["rule"] == "M5" and "12×" in v["explanation"] for v in r["violations"])


def test_customer_visible_play_on_legal_hold_is_P2(ev):
    d = happy_dossier()
    d["decision"].update(recommended_play="csm_checkin", customer_visible=True)
    ev.cx.accounts["acct_T"]["flags"] = ["legal_hold"]
    try:
        assert "P2" in rules(ev.evaluate(d))
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_restricted_quote_when_routed_is_P3(ev):
    """The artefact record is authoritative for `restricted`, not the dossier's own flag."""
    d = happy_dossier()
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, restricted=True)
    try:
        assert "P3" in rules(ev.evaluate(d))
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_dossier_restricted_flag_alone_is_not_P3_when_artifact_says_otherwise(ev):
    d = happy_dossier()
    d["evidence"][0]["restricted"] = True
    assert "P3" not in rules(ev.evaluate(d))


def test_high_confidence_single_source_is_P5(ev):
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    assert "P5" in rules(ev.evaluate(d))


def test_email_in_routed_quote_is_P7(ev):
    d = happy_dossier()
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text="Call me. ada@example.com")
    d["evidence"][0]["quote"] = "Call me. ada@example.com"
    try:
        assert "P7" in rules(ev.evaluate(d))
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
    assert any("legacy double-count" in v["explanation"] for v in r["violations"] if v["rule"] == "M6")


def test_degraded_window_is_unverifiable():
    e = SignalEvaluator(use_classifier=False)
    rows = telemetry([100] * 7, [60] * 7)
    rows[10]["ingest_status"] = "degraded"
    e.load_context([ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 2, "metric": "dau_seats", "window_days": 7, "as_of": "2026-03-01",
                             "value_before": 700, "value_after": 420, "claim": "dau_seats -40% week over week"}]
    assert e.evaluate(d)["_facts"]["claim_status"] == ["unverifiable"]


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
    assert "Q5" in rules(e.evaluate(second))
    assert "Q5" not in rules(e.evaluate(first))
    assert "Q5" not in rules(e.evaluate(third))


def test_billing_dispute_suppressed_is_P1():
    bill = dict(ARTIFACT, artifact_id="art_BILL", type="billing_event", source="billing_event", author_type="bot",
                text="Invoice INV-9 for $20,000 issued. Status: disputed by customer AP.")
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT, bill], [])
    d = happy_dossier()
    d["detector"] = "billing_dispute"
    d["evidence"] = [{"step": 2, "artifact_id": "art_BILL", "source": "billing_event", "restricted": False,
                      "attached_at": "2026-03-02T09:30:00Z", "quote": "Status: disputed by customer AP."}]
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": "immaterial"}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    r = e.evaluate(d)
    assert "P1" in rules(r) and "billing_dispute" in r["_facts"]["triggers"]


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
