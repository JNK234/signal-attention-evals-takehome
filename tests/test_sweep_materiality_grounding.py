"""
ABOUTME: Adversarial sweep of materiality (spec §9 M1–M5), quantitative grounding (§9 M6 with docs/domain.md
ABOUTME: events) and duplicate signals (§10 Q5): exact boundaries, odd windows, unknown metrics, empty telemetry.
"""

import copy
from datetime import date, timedelta

import pytest

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, drop_days, explain, happy_dossier, rules, telemetry
from signal_eval.spec import MIN_PAIRS, SEV_WEIGHT, UNCERTAIN_FACTOR

END = date(2026, 3, 1)


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def _eval(rows, accounts=None):
    e = SignalEvaluator(use_classifier=False)
    e.load_context(accounts or [ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    return e


def _claim(claim, metric="dau_seats", as_of="2026-03-01", window_days=7):
    return {"step": 2, "metric": metric, "window_days": window_days, "as_of": as_of, "value_before": None, "value_after": None, "claim": claim}


def _run(rows, claim, accounts=None, **kw):
    d = happy_dossier()
    d["metrics_claimed"] = [_claim(claim, **kw)]
    r = _eval(rows, accounts).explain(d)
    return r, (r["_facts"]["claim_detail"] or [None])[0], only(r, "M6")


# ── M1–M5 ───────────────────────────────────────────────────────────────────────────────────────

def test_M4_routed_at_exactly_the_floor_is_material(ev):
    """spec §9 M3: floor ≤ arr_at_risk is a closed bound — 18,000 on an 18,000 floor is material, 17,999 is M4."""
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 18_000
    d["actions"][2]["params"]["arr_at_risk"] = 18_000
    assert not {"M3", "M4"} & rules(ev.evaluate(d))
    d["scoring"]["arr_at_risk"] = 17_999
    d["actions"][2]["params"]["arr_at_risk"] = 17_999
    assert len(only(ev.evaluate(d), "M4")) == 1


def test_M1_cold_path_uses_metadata_arr_annual():
    """Without load_context the account record is the metadata snapshot (arr_annual 100,000): 250,000 at risk → M1."""
    d = happy_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = SignalEvaluator(labeller=None).evaluate(d)
    assert "M1" in rules(r) and "100,000" in only(r, "M1")[0]["explanation"]


def test_M5_arr_annual_restated_as_arr_at_risk(ev):
    """spec §9 M5 'must not confuse arr_annual with arr_at_risk': scored 30,000, later stated $100,000 (= arr_annual)
    → M5 certain, naming the confusion."""
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 6, "metric": "arr_at_risk", "window_days": 0, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $100,000"}]
    m5 = only(ev.evaluate(d), "M5")
    assert len(m5) == 1 and m5[0]["step"] == 6 and "arr_annual restated" in m5[0]["explanation"] and m5[0]["severity"] == SEV_WEIGHT["high"]


def test_M5_score_signal_params_disagreeing_with_scoring_block(ev):
    """spec §9 M5 'every later reference … must be consistent': score_signal says 25,000, scoring says 30,000 → M5."""
    d = happy_dossier()
    d["actions"][2]["params"]["arr_at_risk"] = 25_000
    m5 = only(ev.evaluate(d), "M5")
    assert len(m5) == 1 and m5[0]["step"] == 5 and "25,000" in m5[0]["explanation"] and "30,000" in m5[0]["explanation"]


def test_M5_consistent_restatement_is_not_M5(ev):
    """A restatement equal to the scored figure is consistent; a $ figure in a telemetry claim is not a restatement."""
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 6, "metric": "arr_at_risk", "window_days": 0, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $30,000"}]
    assert "M5" not in rules(ev.evaluate(d))


def test_M5_twelvefold_in_the_other_direction_is_still_the_MRR_bug(ev):
    """§9 M5 'a twelve-fold restatement is a bug': scored 30,000, later $2,500 (÷12) → M5 '12×'."""
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 6, "metric": "arr_at_risk", "window_days": 0, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $2,500"}]
    m5 = only(ev.evaluate(d), "M5")
    assert len(m5) == 1 and "12×" in m5[0]["explanation"]


def test_M2_floor_above_arr_on_cold_path_from_metadata():
    """§9 M2 with only the metadata snapshot: floor 150,000 > arr_annual 100,000 → M2."""
    d = happy_dossier()
    d["metadata"]["materiality_floor"] = 150_000
    assert "M2" in rules(SignalEvaluator(labeller=None).evaluate(d))


# ── M6: windows other than 7 ────────────────────────────────────────────────────────────────────

def test_M6_three_day_window_uses_its_own_pair_minimum():
    """spec §9 M6 says nothing that ties a claim to 7 days. window_days 3 as_of 03-01 on the 14-day fixture
    (100 ×7 then 60 ×7): after 02-27..03-01 = 60s, before 02-24..02-26 = 60s → 0% on 3 pairs, MIN_PAIRS(3) = 2.
    A 0% claim is grounded; a −40% claim is wrong."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats 0% week over week", window_days=3)
    assert rec["status"] == "grounded" and rec["n_pairs"] == 3 and rec["min_pairs"] == MIN_PAIRS(3) == 2 and not m6
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats -40% week over week", window_days=3)
    assert rec["status"] == "wrong" and len(m6) == 1


def test_M6_one_day_window_is_a_single_pair():
    """window_days 1 as_of 03-01: 03-01 (60) vs 02-28 (60) → 0%, one pair, minimum 1."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats 0%", window_days=1)
    assert rec["status"] == "grounded" and rec["n_pairs"] == 1 and rec["min_pairs"] == 1


def test_M6_window_days_zero_on_a_telemetry_metric_is_not_silently_seven():
    """docs/data_dictionary.md: window_days 0 appears only on arr_at_risk restatements. A telemetry claim with
    window 0 has no window to reproduce; spec §9 M6 says every entry must be reproducible, so the honest status
    is unverifiable (or wrong), not a grounded verdict computed on a 7-day window the claim never named."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats -40% week over week", window_days=0)
    assert rec["status"] != "grounded", rec


# ── M6: metric not in telemetry ─────────────────────────────────────────────────────────────────

def test_M6_claim_on_a_metric_telemetry_does_not_carry_is_unverifiable_not_ignored():
    """spec §9 M6: 'Every entry in metrics_claimed must be reproducible from telemetry.jsonl'. A claim on
    'nps_score' (not a telemetry field) cannot be reproduced. Expected: it appears in claim_detail as unverifiable
    with an M6 at half weight — not silently dropped, which would let any misspelt metric name evade M6."""
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("nps_score -40% week over week", metric="nps_score")]
    r = _eval(telemetry([100] * 7, [60] * 7)).explain(d)
    assert r["_facts"]["claim_status"] == ["unverifiable"]
    m6 = only(r, "M6")
    assert len(m6) == 1 and m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)


def test_M6_arr_at_risk_restatement_is_not_a_telemetry_claim():
    """Adjacent negative: metric arr_at_risk is M5's business (data dictionary) and must not appear in M6's
    claim_detail at all."""
    d = happy_dossier()
    d["metrics_claimed"] = [{"step": 6, "metric": "arr_at_risk", "window_days": 0, "as_of": None, "value_before": None,
                             "value_after": None, "claim": "ARR at risk restated as $30,000"}]
    r = _eval(telemetry([100] * 7, [60] * 7)).explain(d)
    assert r["_facts"]["claim_status"] == [] and "M6" not in rules(r)


# ── M6: no usable data ──────────────────────────────────────────────────────────────────────────

def test_M6_before_window_entirely_missing_is_unverifiable():
    """All seven before-days absent → 0 pairs (< MIN_PAIRS 4), raw baseline 0 → unverifiable, half weight,
    coverage stated as 0/7."""
    rows = drop_days(telemetry([100] * 7, [60] * 7), *(END - timedelta(days=i) for i in range(7, 14)))
    r, rec, m6 = _run(rows, "dau_seats -40% week over week")
    assert rec["status"] == "unverifiable" and rec["n_pairs"] == 0
    assert len(m6) == 1 and "0/7 clean pairs" in m6[0]["explanation"] and m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)


def test_M6_every_row_degraded_is_unverifiable_naming_the_exclusion():
    """spec §9 M6 'excluded windows where ingest_status is not ok': 14 degraded rows → 0 pairs, 14 excluded as
    degraded; the raw sum (uncorrected, not excluded) still reproduces −40% but with nothing clean left the verdict
    is an uncertain artifact-or-unverifiable — never grounded."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7, status="degraded"), "dau_seats -40% week over week")
    assert rec["status"] in ("unverifiable", "artifact") and rec["n_pairs"] == 0 and rec["excluded"] == {"degraded": 14}
    assert len(m6) == 1 and m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * UNCERTAIN_FACTOR)
    assert "14 day(s) degraded" in m6[0]["explanation"]


def test_M6_account_with_no_telemetry_at_all_is_unverifiable():
    """The corpus is loaded but this account has no rows: the claim cannot be reproduced → unverifiable, 0/7."""
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -40% week over week")]
    r = _eval(telemetry([100] * 7, [60] * 7, account_id="acct_SOMEONE_ELSE")).explain(d)
    assert r["_facts"]["claim_status"] == ["unverifiable"] and "0/7" in only(r, "M6")[0]["explanation"]


def test_M6_claim_without_a_percentage_or_date_is_unverifiable_with_a_finding():
    """spec §9 M6 needs a stated change to compare against. 'dau_seats fell sharply' (no %) or a claim with no as_of
    is unverifiable; spec §9 M6 says every entry must be reproducible, so this should surface as an M6 finding at
    half weight, not a silent status."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats fell sharply")
    assert rec["status"] == "unverifiable"
    assert len(m6) == 1, "an unreproducible claim with no percentage leaves no M6 entry"


# ── M6: documented events and windows that do / do not touch them ───────────────────────────────

def _flat(end, api=1000):
    rows = telemetry([50] * 7, [50] * 7, end=end)
    for r in rows:
        r["api_calls"] = api
    return rows


def test_M6_legacy_account_window_entirely_after_the_migration_names_no_event():
    """docs/domain.md: the double-count ended 2026-05-18. A legacy account's api_calls claim as_of 06-07 (span
    05-25..06-07) is untouched by it: flat 1000/day → 0% grounded and no 'legacy' event in the record."""
    r, rec, m6 = _run(_flat(date(2026, 6, 7)), "api_calls 0% week over week", metric="api_calls", as_of="2026-06-07")
    assert rec["status"] == "grounded" and rec["paired_pct"] == pytest.approx(0.0) and not m6
    assert not any("legacy" in e for e in rec["events"])


def test_M6_legacy_account_window_entirely_before_the_migration_is_corrected_on_both_sides():
    """Both weeks before 05-18 are halved alike, so the corrected change equals the raw change: 2000 → 1000 flat
    across 04-27..05-10 is 0% either way, grounded, and the event is not named (it lies outside the span)."""
    r, rec, m6 = _run(_flat(date(2026, 5, 10), api=2000), "api_calls 0% week over week", metric="api_calls", as_of="2026-05-10")
    assert rec["status"] == "grounded" and rec["paired_pct"] == pytest.approx(0.0) and not m6
    assert not any("legacy" in e for e in rec["events"])


def test_M6_v2_collector_is_never_halved():
    """docs/domain.md: only the legacy collector double-counted. A v2 account with 2000 → 1000 across 05-18 really
    halved: paired −50% grounded on a −50% claim, and no legacy event."""
    rows = telemetry([50] * 7, [50] * 7, end=date(2026, 5, 24))
    for r in rows:
        r["api_calls"] = 2000 if date.fromisoformat(r["date"]) < date(2026, 5, 18) else 1000
    v2 = dict(ACCOUNT, collector="v2")
    r, rec, m6 = _run(rows, "api_calls -50% week over week", accounts=[v2], metric="api_calls", as_of="2026-05-24")
    assert rec["status"] == "grounded" and rec["paired_pct"] == pytest.approx(-50.0) and not m6
    assert not any("legacy" in e for e in rec["events"])


def test_M6_emea_claim_entirely_inside_the_ingest_gap_is_unverifiable_naming_the_gap():
    """docs/domain.md: apac/emea rows 06-11..06-13 are absent. A 3-day emea claim as_of 06-13 has every after-day
    in the gap → 0 pairs (< MIN_PAIRS(3) = 2) → unverifiable (the zero-filled sum reads −100%, which a −30% claim
    does not match, so it is not the 'raw reproduces' artifact branch); the record names the ingest gap, never
    'missing days read as zero'."""
    rows = drop_days(telemetry([50] * 7, [50] * 7, end=date(2026, 6, 13)), date(2026, 6, 11), date(2026, 6, 12), date(2026, 6, 13))
    r, rec, m6 = _run(rows, "dau_seats -30% week over week", window_days=3, as_of="2026-06-13")
    assert rec["status"] == "unverifiable" and rec["n_pairs"] == 0
    assert any("ingest gap" in e and "3 missing days" in e for e in rec["events"])
    assert not any("read as zero" in e for e in rec["events"])


def test_M6_namer_account_missing_the_same_dates_is_not_the_ingest_gap():
    """The gap is regional (apac/emea). A namer account missing 06-11..13 has three ordinary missing days: the
    event list says 'missing day(s) read as zero', never 'ingest gap'."""
    rows = drop_days(telemetry([50] * 7, [50] * 7, end=date(2026, 6, 14)), date(2026, 6, 11), date(2026, 6, 12), date(2026, 6, 13))
    namer = dict(ACCOUNT, region="namer")
    r, rec, m6 = _run(rows, "dau_seats 0% week over week", accounts=[namer], as_of="2026-06-14")
    assert rec["n_pairs"] == 4 and rec["status"] == "grounded"
    assert any("3 missing day(s) read as zero" in e for e in rec["events"]) and not any("ingest gap" in e for e in rec["events"])


def test_M6_missing_day_in_the_before_window_only_is_named_and_raw_inflates_upward():
    """A missing before-day makes the zero-filled baseline smaller, so raw reads a smaller decline than reality
    (600 → 420 = −30%) while the six pairs say −40%. A −30% claim is therefore an artifact: raw reproduces it,
    paired does not, and since paired ≤ −5 it is labelled an 'inflated real decline' (the real decline is bigger)."""
    rows = drop_days(telemetry([100] * 7, [60] * 7), END - timedelta(days=9))
    r, rec, m6 = _run(rows, "dau_seats -30% week over week")
    assert rec["status"] == "artifact" and rec["raw_pct"] == pytest.approx(-30.0) and rec["paired_pct"] == pytest.approx(-40.0)
    assert len(m6) == 1 and "missing day" in m6[0]["explanation"]


# ── M6: cohort boundary ─────────────────────────────────────────────────────────────────────────

def _cohort_corpus(n_peers, peer_drop=0.70):
    accounts = [dict(ACCOUNT)]
    rows = telemetry([100] * 7, [68] * 7)                                          # target −32%
    for i in range(n_peers):
        aid = f"acct_P{i}"
        accounts.append(dict(ACCOUNT, account_id=aid, name=f"Peer {i}", region="latam", industry="x"))
        rows += telemetry([100] * 7, [100 * peer_drop] * 7, account_id=aid)
    return accounts, rows


def test_M6_cohort_of_exactly_five_peers_is_a_cohort_and_four_is_not():
    """COHORT_MIN_ACCOUNTS = 5 (evaluator constant, spec.py): five same-industry peers at −30% → industry match
    n=5; four → no match. The claim stays grounded either way (the cohort is a fact, not a status)."""
    accounts, rows = _cohort_corpus(5)
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and rec["cohort"] and rec["cohort"]["n"] == 5 and rec["cohort"]["key"] == "industry"
    accounts, rows = _cohort_corpus(4)
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and rec["cohort"] is None


def test_M6_cohort_that_did_not_move_is_not_a_match():
    """Five peers flat at 0% while the account fell 32%: median 0 is not ≤ −20 → no cohort fact."""
    accounts, rows = _cohort_corpus(5, peer_drop=1.0)
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["cohort"] is None


# ── Q5 ──────────────────────────────────────────────────────────────────────────────────────────

def _corpus_with(*dossiers):
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], list(dossiers))
    return e


def _sig(sid, opened_at, detector="seat_decay", closed_at=None):
    d = happy_dossier()
    d.update(signal_id=sid, opened_at=opened_at, detector=detector, closed_at=closed_at)
    d["lifecycle"][0]["trigger"] = f"detector:{detector}"
    return d


def test_Q5_different_detector_on_the_same_account_within_a_week_is_not_a_duplicate():
    """spec §10 Q5 'Two open signals on one account with the same detector inside a week'. usage_cliff then
    seat_decay a day later → no Q5."""
    first = _sig("sig_A", "2026-03-02T08:00:00Z", detector="usage_cliff")
    second = _sig("sig_B", "2026-03-03T08:00:00Z")
    assert "Q5" not in rules(_corpus_with(first, second).evaluate(second))


def test_Q5_same_detector_on_another_account_is_not_a_duplicate():
    first = _sig("sig_A", "2026-03-02T08:00:00Z")
    first["account_id"] = "acct_Z"
    second = _sig("sig_B", "2026-03-03T08:00:00Z")
    assert "Q5" not in rules(_corpus_with(first, second).evaluate(second))


def test_Q5_severity_grows_with_the_count_and_caps_at_three():
    """spec §10 Q5 'Severity increases with the count.' Two earlier open duplicates → soft 0.1 × 2/3; three →
    0.1 × 3/3; four → still capped at 0.1 (spec.py Q5_COUNT_CAP)."""
    earlier = [_sig(f"sig_{i}", f"2026-03-0{2 + i}T08:00:00Z") for i in range(4)]
    late = _sig("sig_L", "2026-03-06T09:00:00Z")
    for n in (2, 3, 4):
        e = _corpus_with(*earlier[:n], late)
        q5 = only(e.evaluate(late), "Q5")
        assert len(q5) == 1 and q5[0]["severity"] == pytest.approx(round(0.1 * min(n, 3) / 3, 3)), n


def test_Q5_is_flagged_on_the_later_signal_only():
    first = _sig("sig_A", "2026-03-02T08:00:00Z")
    second = _sig("sig_B", "2026-03-04T08:00:00Z")
    e = _corpus_with(first, second)
    assert "Q5" in rules(e.evaluate(second)) and "Q5" not in rules(e.evaluate(first))
