"""
ABOUTME: Unit tests for the paired-day M6 grounding (spec §9 M6, docs/domain.md events): pair counting,
ABOUTME: raw-vs-paired verdicts, telemetry cleaning flags and the leave-one-out cohort fact.
"""

from datetime import date, timedelta

import pytest

from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, drop_days, happy_dossier, set_row, telemetry
from signal_eval.context import paired_change
from signal_eval.spec import MIN_PAIRS, SEV_WEIGHT
from signal_eval.util import mean

END = date(2026, 3, 1)                       # a Sunday; after window 02-23..03-01, before window 02-16..02-22


def _eval(rows, accounts=None):
    e = SignalEvaluator(use_classifier=False)
    e.load_context(accounts or [ACCOUNT], [OWNER], rows, [ARTIFACT], [])
    return e


def _claim(claim, metric="dau_seats", as_of="2026-03-01"):
    return {"step": 2, "metric": metric, "window_days": 7, "as_of": as_of, "value_before": None, "value_after": None, "claim": claim}


def _run(rows, claim, metric="dau_seats", as_of="2026-03-01", accounts=None):
    d = happy_dossier()
    d["metrics_claimed"] = [_claim(claim, metric, as_of)]
    r = _eval(rows, accounts).explain(d)
    return r, r["_facts"]["claim_detail"][0], [v for v in r["violations"] if v["rule"] == "M6"]


# ── pairing ───────────────────────────────────────────────────────────────────────────────────

def test_constant_decline_is_grounded_on_all_seven_pairs():
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats -40% week over week")
    assert rec["status"] == "grounded" and rec["n_pairs"] == 7 and rec["min_pairs"] == MIN_PAIRS(7) == 4
    assert rec["paired_pct"] == pytest.approx(-40.0) and rec["raw_pct"] == pytest.approx(-40.0)
    assert not m6 and r["_facts"]["claim_status"] == ["grounded"]


def test_one_missing_after_day_drops_its_pair_and_keeps_the_real_change():
    """Raw zero-filled sum reads 360/700 = −48.6%; the six surviving pairs still say −40%."""
    rows = drop_days(telemetry([100] * 7, [60] * 7), END - timedelta(days=2))
    r, rec, m6 = _run(rows, "dau_seats -40% week over week")
    assert rec["status"] == "grounded" and rec["n_pairs"] == 6 and rec["excluded"] == {"missing": 1}
    assert rec["paired_pct"] == pytest.approx(-40.0) and rec["raw_pct"] == pytest.approx(-48.6, abs=0.1)
    assert not m6


def test_claim_equal_to_zero_filled_sum_is_a_full_artifact_stating_the_real_decline():
    rows = drop_days(telemetry([100] * 7, [60] * 7), END - timedelta(days=2))
    r, rec, m6 = _run(rows, "dau_seats -49% week over week")
    assert rec["status"] == "artifact" and len(m6) == 1
    assert m6[0]["severity"] == SEV_WEIGHT["high"] and m6[0]["step"] == 2
    x = m6[0]["explanation"]
    assert "6/7" in x and "inflated real decline" in x
    assert "claimed -49%" in x and "raw -48.6%" in x and "paired -40.0% on 6/7 pairs" in x and "missing day" in x


def test_missing_before_day_removes_its_pair():
    """The day 9 before `end` is missing: its partner (day 2) is present but unpaired → 6 pairs, still −40%."""
    rows = drop_days(telemetry([100] * 7, [60] * 7), END - timedelta(days=9))
    r, rec, m6 = _run(rows, "dau_seats -40% week over week")
    assert rec["status"] == "grounded" and rec["n_pairs"] == 6 and rec["excluded"] == {"missing": 1}
    assert rec["paired_pct"] == pytest.approx(-40.0)


def test_below_the_pair_minimum_is_unverifiable_with_coverage_stated():
    """Four consecutive missing after-days leave 3 of 7 pairs, under the strict majority MIN_PAIRS(7) = 4.
    (Three missing days leave 4 pairs, which is a majority and stays computable.)"""
    rows = drop_days(telemetry([100] * 7, [60] * 7), *(END - timedelta(days=i) for i in range(1, 5)))
    r, rec, m6 = _run(rows, "dau_seats -20% week over week")
    assert rec["status"] == "unverifiable" and rec["n_pairs"] == 3 and rec["paired_pct"] == pytest.approx(-40.0)
    assert len(m6) == 1 and m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * 0.5)
    assert "3/7 clean pairs (min 4)" in m6[0]["explanation"]


def test_below_the_pair_minimum_with_raw_reproducing_is_an_uncertain_artifact():
    """Same four-day gap; the zero-filled sum is 180/700 = −74.3% and the claim says −74%."""
    rows = drop_days(telemetry([100] * 7, [60] * 7), *(END - timedelta(days=i) for i in range(1, 5)))
    r, rec, m6 = _run(rows, "dau_seats -74% week over week")
    assert rec["status"] == "artifact" and rec["n_pairs"] == 3
    assert len(m6) == 1 and m6[0]["severity"] == pytest.approx(SEV_WEIGHT["high"] * 0.5)
    x = m6[0]["explanation"]
    assert "raw zero-filled sum reproduces the claim" in x and "3/7 clean pairs (min 4)" in x and "missing day" in x


def test_weekend_composition_does_not_bias_the_paired_change():
    """Weekday 100 / weekend 20 in both weeks: nothing changed, paired says 0%. A clean-day mean over a
    5-vs-7 day cut (Mon and Tue missing) would read the same data as a double-digit decline."""
    shape = lambda d: 20 if d.weekday() >= 5 else 100
    before = [shape(END - timedelta(days=13 - i)) for i in range(7)]
    after = [shape(END - timedelta(days=6 - i)) for i in range(7)]
    rows = drop_days(telemetry(before, after), END - timedelta(days=6), END - timedelta(days=5))
    r, rec, m6 = _run(rows, "dau_seats 0% week over week")
    assert rec["status"] == "grounded" and rec["n_pairs"] == 5 and rec["paired_pct"] == pytest.approx(0.0)
    clean_after = [shape(END - timedelta(days=i)) for i in range(5)]
    naive = (mean(clean_after) - mean(before)) / mean(before) * 100
    assert naive < -10


def test_degraded_day_is_excluded_from_pairs():
    rows = set_row(telemetry([100] * 7, [60] * 7), END - timedelta(days=3), ingest_status="degraded")
    r, rec, m6 = _run(rows, "dau_seats -40% week over week")
    assert rec["status"] == "grounded" and rec["n_pairs"] == 6 and rec["excluded"] == {"degraded": 1}
    assert not m6


# ── row flags ─────────────────────────────────────────────────────────────────────────────────

def test_dead_collector_means_every_volume_metric_is_zero():
    """docs/domain.md: 'every metric is genuinely zero while the status still reads ok'. Latency can still be
    reported by a collector that has stopped counting, so dead = api_calls, dau_seats and data_volume_gb all 0."""
    rows = telemetry([100] * 7, [60] * 7)
    set_row(rows, END - timedelta(days=1), api_calls=0, dau_seats=0, data_volume_gb=0, query_p95_ms=400)
    set_row(rows, END - timedelta(days=2), api_calls=0, dau_seats=0, data_volume_gb=1.2)
    tel = _eval(rows).cx.tel["acct_T"]
    dead, alive = tel[END - timedelta(days=1)], tel[END - timedelta(days=2)]
    assert dead["dead"] is True and dead["usable"] is False and dead["bad_reason"] == "dead collector"
    assert alive["dead"] is False and alive["usable"] is True


def test_seats_overshoot_is_recorded_but_still_pairs():
    """dau_seats above 1.05 × seats_contracted is a fact, not an exclusion — spec §9 M6 names none."""
    rows = set_row(telemetry([100] * 7, [60] * 7), END - timedelta(days=1), dau_seats=120)
    e = _eval(rows)
    assert e.cx.tel["acct_T"][END - timedelta(days=1)]["seats_overshoot"] is True
    assert e.cx.tel["acct_T"][END - timedelta(days=2)]["seats_overshoot"] is False
    d = happy_dossier()
    d["metrics_claimed"] = [_claim("dau_seats -31% week over week")]           # (6×60 + 120) / 700 = −31.4%
    rec = e.explain(d)["_facts"]["claim_detail"][0]
    assert rec["status"] == "grounded" and rec["n_pairs"] == 7 and rec["seats_overshoot_days"] == 1


def test_raw_sum_survives_a_row_missing_the_metric_field():
    rows = telemetry([100] * 7, [60] * 7)
    for r in rows:
        if r["date"] == (END - timedelta(days=1)).isoformat():
            del r["dau_seats"]
    r, rec, m6 = _run(rows, "dau_seats -40% week over week")
    assert r["_facts"]["errors"] == []
    assert rec["status"] == "grounded" and rec["n_pairs"] == 6 and rec["raw_pct"] == pytest.approx(-48.6, abs=0.1)


def test_paired_change_direct():
    rows = {END - timedelta(days=i): {"dau_seats": 60 if i < 7 else 100, "usable": True} for i in range(14)}
    rows[END - timedelta(days=4)]["usable"], rows[END - timedelta(days=4)]["bad_reason"] = False, "dead collector"
    p = paired_change(rows, "dau_seats", END, 7)
    assert p["n_pairs"] == 6 and p["pct"] == pytest.approx(-40.0) and p["excluded"] == {"dead collector": 1}
    assert p["sum_after"] == 360 and p["sum_before"] == 600
    assert paired_change({}, "dau_seats", END, 7) == {"pct": None, "n_pairs": 0, "sum_after": 0.0, "sum_before": 0.0, "excluded": {"missing": 14}}


# ── documented events are named ───────────────────────────────────────────────────────────────

def test_legacy_double_count_end_is_named():
    """Legacy collector, api_calls 2000 → 1000 across 2026-05-18: corrected paired change 0%, raw −50%."""
    end = date(2026, 5, 24)
    rows = telemetry([50] * 7, [50] * 7, end=end)
    for r in rows:
        r["api_calls"] = 2000 if date.fromisoformat(r["date"]) < date(2026, 5, 18) else 1000
    r, rec, m6 = _run(rows, "api_calls -50% week over week", metric="api_calls", as_of="2026-05-24")
    assert rec["status"] == "artifact" and rec["paired_pct"] == pytest.approx(0.0) and rec["raw_pct"] == pytest.approx(-50.0)
    assert any("legacy double-count ended 2026-05-18" in e for e in rec["events"])
    assert "manufactured decline" in m6[0]["explanation"] and "legacy" in m6[0]["explanation"]


def test_p95_instrumentation_step_is_named():
    """query_p95_ms 500 → 900 across 2026-04-27: corrected (×1.8 before) paired change 0%, raw +80%."""
    end = date(2026, 5, 3)
    rows = telemetry([50] * 7, [50] * 7, end=end)
    for r in rows:
        r["query_p95_ms"] = 500 if date.fromisoformat(r["date"]) < date(2026, 4, 27) else 900
    r, rec, m6 = _run(rows, "query_p95_ms +80% week over week", metric="query_p95_ms", as_of="2026-05-03")
    assert rec["status"] == "artifact" and rec["paired_pct"] == pytest.approx(0.0) and rec["raw_pct"] == pytest.approx(80.0)
    assert any("p95 instrumentation step 2026-04-27" in e for e in rec["events"])


def test_wrong_claim_states_all_three_numbers():
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats -60% week over week")
    assert rec["status"] == "wrong" and len(m6) == 1 and m6[0]["severity"] == SEV_WEIGHT["high"]
    x = m6[0]["explanation"]
    assert "claimed -60%" in x and "paired -40" in x and "raw -40" in x and "7/7" in x


# ── cohort: leave-one-out median by industry and by region ────────────────────────────────────

def _cohort_corpus(peer_regions, peer_industry="x", peer_drop=0.70):
    accounts = [dict(ACCOUNT)]
    rows = telemetry([100] * 7, [68] * 7)                                        # target −32%
    for i, region in enumerate(peer_regions):
        aid = f"acct_P{i}"
        accounts.append(dict(ACCOUNT, account_id=aid, name=f"Peer {i}", region=region, industry=peer_industry))
        rows += telemetry([100] * 7, [100 * peer_drop] * 7, account_id=aid)
    return accounts, rows


def test_cohort_match_by_industry_leaves_the_account_out_and_region_needs_five():
    """Six peers in the same industry at −30% (target −32%) → industry match on n=6 (the target itself is not
    in its own cohort). Only three peers share the region → no region cohort."""
    accounts, rows = _cohort_corpus(["emea", "emea", "emea", "latam", "latam", "latam"])
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and not m6
    assert rec["cohort"]["key"] == "industry" and rec["cohort"]["value"] == "x" and rec["cohort"]["n"] == 6
    assert rec["cohort"]["median_pct"] == pytest.approx(-30.0) and rec["cohort"]["metric"] == "dau_seats"


def test_region_cohort_of_three_is_not_a_cohort():
    accounts, rows = _cohort_corpus(["emea", "emea", "emea"], peer_industry="other")
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and rec["cohort"] is None


def test_cohort_match_does_not_change_the_claim_status():
    accounts, rows = _cohort_corpus(["emea"] * 6)
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and rec["cohort"] is not None
    assert r["_facts"]["claim_status"] == ["grounded"] and "cohort" not in r["_facts"]["claim_status"]


def test_cohort_peer_below_pair_minimum_is_not_counted():
    """Five peers, one of them with only 3 clean pairs → cohort n=4 < 5 → no match."""
    accounts, rows = _cohort_corpus(["emea"] * 5)
    peers = [x for x in rows if x["account_id"] != "acct_T"]
    thin = drop_days([x for x in peers if x["account_id"] == "acct_P0"], *(END - timedelta(days=i) for i in range(1, 5)))
    rows = [x for x in rows if x["account_id"] != "acct_P0"] + thin
    r, rec, m6 = _run(rows, "dau_seats -32% week over week", accounts=accounts)
    assert rec["status"] == "grounded" and rec["cohort"] is None


# ── the sign of a claim must survive how the author typed the minus ───────────

@pytest.mark.parametrize("dash,name", [
    ("−", "minus sign U+2212"),
    ("–", "en dash U+2013"),
    ("‐", "hyphen U+2010"),
])
def test_a_decline_written_with_a_typographic_minus_is_still_a_decline(dash, name):
    """A word processor turns "-40%" into "−40%". The telemetry did not change, so the verdict must not:
    read as +40% the claim is 80pp from the truth and the agent is accused of a wrong number it never made."""
    rows = telemetry([100] * 7, [60] * 7)
    ascii_r, ascii_rec, ascii_m6 = _run(rows, "dau_seats -40% week over week")
    fancy_r, fancy_rec, fancy_m6 = _run(rows, f"dau_seats {dash}40% week over week")
    assert ascii_rec["status"] == "grounded" and not ascii_m6            # the control
    assert fancy_rec["status"] == "grounded", f"{name}: {fancy_rec.get('why') or fancy_rec}"
    assert not fancy_m6, f"{name} was reported as a wrong claim: {fancy_m6}"
    assert fancy_rec["claimed_pct"] == ascii_rec["claimed_pct"]


def test_a_rise_claimed_against_a_fall_is_still_caught_after_the_dash_fix():
    """The guard on the fix: making dashes negative must not make every claim pass. A genuine +40%
    claim against a 40% fall is 80pp wrong and must still fire M6."""
    r, rec, m6 = _run(telemetry([100] * 7, [60] * 7), "dau_seats +40% week over week")
    assert rec["status"] == "wrong" and len(m6) == 1
