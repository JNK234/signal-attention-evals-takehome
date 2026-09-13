"""
ABOUTME: Regression tests for the grounding (M6), staleness (T4), hypothesis-fit / hygiene (Q2, Q4)
ABOUTME: and duplicate-signal (Q5) review findings. Each test breaks one thing and asserts one rule.
"""

import copy
from datetime import date, timedelta

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, explain, happy_dossier, rules, telemetry


def _grounding_eval(rows):
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    return e


def _claim(claim, metric="dau_seats", as_of="2026-03-01", step=2):
    return {"step": step, "metric": metric, "window_days": 7, "as_of": as_of, "value_before": None, "value_after": None, "claim": claim}


def _by_rule(result, rule):
    return [v for v in result["violations"] if v["rule"] == rule]


# ── M6: tolerance is exactly 5.0 percentage points (spec §9 M6) ─────────────────────────────

def test_claim_5_4pp_off_is_not_grounded():
    e = _grounding_eval(telemetry([100] * 7, [60.4] * 7))          # corrected change −39.6%
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -45% week over week")]
    r = explain(e, d)
    assert r["_facts"]["claim_status"] == ["wrong"]
    m6 = _by_rule(r, "M6")
    assert len(m6) == 1 and m6[0]["step"] == 2
    assert "claimed -45%" in m6[0]["explanation"] and "corrected telemetry -40%" in m6[0]["explanation"]


def test_claim_exactly_5pp_off_is_grounded():
    e = _grounding_eval(telemetry([100] * 7, [60] * 7))            # corrected change −40.0%
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -45% week over week")]
    r = explain(e, d)
    assert r["_facts"]["claim_status"] == ["grounded"] and not _by_rule(r, "M6")


# ── M6: −100% is a rate falling to zero; only below −100% is impossible ───────────────────

def test_claim_of_minus_100_is_grounded_when_metric_fell_to_zero():
    e = _grounding_eval(telemetry([100] * 7, [0] * 7))             # api_calls stay 1000, so rows are not "dead"
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -100% week over week")]
    r = explain(e, d)
    assert r["_facts"]["claim_status"] == ["grounded"] and not _by_rule(r, "M6")


def test_claim_below_minus_100_is_arithmetically_impossible():
    e = _grounding_eval(telemetry([100] * 7, [0] * 7))
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -104% week over week")]
    r = e.evaluate(d)
    m6 = _by_rule(r, "M6")
    assert len(m6) == 1 and m6[0]["step"] == 2 and "arithmetically impossible" in m6[0]["explanation"]


# ── M6: backfill rows stay usable, but the claim detail must count them ──────────────────────

def test_claim_detail_reports_backfill_days():
    rows = telemetry([100] * 7, [60] * 7)
    fixed = dict(rows[9], ingest_status="backfill", ingested_at="2026-03-05T03:00:00Z")   # correction lands later
    e = _grounding_eval(rows + [fixed])
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -40% week over week")]
    r = explain(e, d)
    assert r["_facts"]["claim_status"] == ["grounded"]              # the survivor is the correction; still usable
    assert r["_facts"]["claim_detail"][0]["backfill_days"] == 1


def test_claim_detail_backfill_days_is_zero_without_corrections():
    e = _grounding_eval(telemetry([100] * 7, [60] * 7))
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -40% week over week")]
    assert explain(e, d)["_facts"]["claim_detail"][0]["backfill_days"] == 0


# ── T4: idle time is measured to the last evidence attached BEFORE expiry (spec §6.4) ─────────

def _expired(d, at):
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": at, "trigger": "staleness_timeout", "reason": ""}
    d["decision"]["disposition"] = "expired"
    return d


def test_evidence_attached_after_expiry_does_not_make_expiry_premature(ev):
    d = _expired(happy_dossier(), "2026-03-20T12:00:00Z")          # 18 idle days after the 03-02 evidence
    d["evidence"].append(dict(d["evidence"][0], step=8, attached_at="2026-03-25T09:00:00Z"))
    assert not _by_rule(ev.evaluate(d), "T4")


def test_premature_expiry_is_T4(ev):
    d = _expired(happy_dossier(), "2026-03-10T12:00:00Z")          # 8 idle days
    t4 = _by_rule(ev.evaluate(d), "T4")
    assert len(t4) == 1 and t4[0]["step"] == 7 and "expired after 8 idle days" in t4[0]["explanation"]


def test_evidence_attached_before_expiry_counts_toward_idle_time(ev):
    d = _expired(happy_dossier(), "2026-03-20T12:00:00Z")
    d["evidence"].append(dict(d["evidence"][0], step=7, attached_at="2026-03-15T09:00:00Z"))   # 5 idle days
    t4 = _by_rule(ev.evaluate(d), "T4")
    assert len(t4) == 1 and t4[0]["step"] == 7 and "expired after 5 idle days" in t4[0]["explanation"]


# ── T4: a still-open signal is judged on evidence up to the last observed event ──────────────

def _still_open(d):
    d["lifecycle"] = d["lifecycle"][:-1]                                   # ends at routed, never acknowledged
    d["lifecycle"][-1]["at"] = "2026-03-20T14:00:00Z"                     # last observed event, 18 days after evidence
    d["notifications"][0]["at"] = "2026-03-20T14:00:00Z"
    d["actions"][3]["at"] = "2026-03-20T14:00:00Z"
    d["closed_at"] = None
    d["decision"]["disposition"] = "routed"
    return d


def test_still_open_overdue_is_T4(ev):
    d = _still_open(happy_dossier())
    t4 = _by_rule(ev.evaluate(d), "T4")
    assert len(t4) == 1 and t4[0]["step"] == 6 and "still open 18 days after the last evidence" in t4[0]["explanation"]


def test_evidence_after_last_observed_event_does_not_reset_overdue(ev):
    d = _still_open(happy_dossier())
    d["evidence"].append(dict(d["evidence"][0], step=8, attached_at="2026-03-25T09:00:00Z"))
    t4 = _by_rule(ev.evaluate(d), "T4")
    assert len(t4) == 1 and "still open 18 days" in t4[0]["explanation"]


def test_still_open_with_recent_evidence_is_not_T4(ev):
    d = _still_open(happy_dossier())
    d["evidence"].append(dict(d["evidence"][0], step=6, attached_at="2026-03-18T09:00:00Z"))   # 2 idle days
    assert not _by_rule(ev.evaluate(d), "T4")


# ── Q2: p95 claim spanning the instrumentation step (spec §10 Q2) ───────────────────────────

def _p95_dossier():
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "reliability_erosion"
    d["metrics_claimed"] = [_claim("query_p95_ms +80% week over week", metric="query_p95_ms", as_of="2026-05-01")]
    return d


def _p95_q2(result):
    return [v for v in _by_rule(result, "Q2") if "instrumentation change" in v["explanation"]]


def test_p95_span_is_not_Q2_when_customer_text_backs_the_hypothesis(ev):
    r = explain(ev, _p95_dossier())                                       # art_T1 is customer-authored and verified
    assert r["_facts"]["has_customer_text"] is True and not _p95_q2(r)


def test_p95_span_is_Q2_without_any_evidence(ev):
    d = _p95_dossier()
    d["evidence"] = []
    q2 = _p95_q2(ev.evaluate(d))
    assert len(q2) == 1 and q2[0]["step"] == 2 and "2026-04-27" in q2[0]["explanation"]


def test_p95_span_is_Q2_when_only_bot_text_is_attached(ev):
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, author_type="bot")
    try:
        r = explain(ev, _p95_dossier())
        assert r["_facts"]["has_customer_text"] is False and len(_p95_q2(r)) == 1
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


# ── Q4: re-request means asking again AFTER enrichment was returned (spec §10 Q4) ───────────

def _q4_rerequest(result):
    return [v for v in _by_rule(result, "Q4") if "enrichment" in v["explanation"]]


def test_retry_before_enrichment_returned_is_not_Q4(ev):
    d = happy_dossier()                                                    # returned at step 4, 12:00
    d["actions"].insert(2, {"step": 3, "action": "request_enrichment", "at": "2026-03-02T11:30:00Z", "params": {}})
    assert not _q4_rerequest(ev.evaluate(d))


def test_request_after_enrichment_returned_is_Q4(ev):
    d = happy_dossier()
    d["actions"].insert(3, {"step": 4, "action": "request_enrichment", "at": "2026-03-02T12:30:00Z", "params": {}})
    q4 = _q4_rerequest(ev.evaluate(d))
    assert len(q4) == 1 and q4[0]["step"] == 4 and "already returned" in q4[0]["explanation"]


def test_single_request_is_not_Q4(ev):
    assert not _q4_rerequest(ev.evaluate(happy_dossier()))


# ── Q5: the earlier signal is open iff closed_at is None or closed_at > later opened_at ──────

def _pair(second_opened_at):
    first = happy_dossier()                                                # closed 2026-03-03T12:00:00Z
    second = copy.deepcopy(first)
    second["signal_id"] = "sig_T2"
    second["opened_at"] = second_opened_at
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [first, second])
    return e, second


def test_signal_opened_exactly_when_earlier_closes_is_not_Q5():
    e, second = _pair("2026-03-03T12:00:00Z")
    r = explain(e, second)
    assert not _by_rule(r, "Q5") and r["_facts"]["duplicates"] == []


def test_signal_opened_one_minute_before_earlier_closes_is_Q5():
    e, second = _pair("2026-03-03T11:59:00Z")
    q5 = _by_rule(e.evaluate(second), "Q5")
    assert len(q5) == 1 and q5[0]["step"] == 0 and "duplicate of ['sig_T']" in q5[0]["explanation"]


def test_earlier_signal_never_closed_is_Q5():
    first = happy_dossier()
    first["closed_at"] = None
    second = copy.deepcopy(first)
    second["signal_id"] = "sig_T2"
    second["opened_at"] = "2026-03-06T08:00:00Z"
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [first, second])
    assert "Q5" in rules(e.evaluate(second))


# ── Q5: "inside a week" is Δ ≤ 7×24h inclusive; the earlier signal must still be open (half-open) ──

def _open_pair(second_opened_at):
    first = happy_dossier()                                                # opened 2026-03-02T08:00:00Z, never closed
    first["closed_at"] = None
    second = copy.deepcopy(first)
    second["signal_id"] = "sig_T2"
    second["opened_at"] = second_opened_at
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [first, second])
    return e.evaluate(second)


def test_signal_opened_exactly_seven_days_later_is_Q5():
    """spec §10 Q5 'inside a week': Δ = 7.00 days is on the closed bound. One duplicate → soft weight × 1/3."""
    q5 = _by_rule(_open_pair("2026-03-09T08:00:00Z"), "Q5")
    assert len(q5) == 1 and q5[0]["severity"] == round(0.1 * 1 / 3, 3)


def test_signal_opened_seven_days_and_one_minute_later_is_not_Q5():
    assert not _by_rule(_open_pair("2026-03-09T08:01:00Z"), "Q5")
