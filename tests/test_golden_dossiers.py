"""
ABOUTME: Golden tests — ten real dossiers whose expected layer-1 output was derived BY HAND from the raw
ABOUTME: JSON (lifecycle, artefact text, telemetry recompute, account/owner records) against spec.tex,
ABOUTME: before running the evaluator. Each test states the raw facts it rests on. Needs data/ present.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_eval import SignalEvaluator  # noqa: E402

DATA = ROOT / "data"
CACHE = ROOT / "analysis" / ".cache" / "labels.json"
pytestmark = pytest.mark.skipif(not (DATA / "signal_dossiers.jsonl").exists(), reason="corpus not present")


def _load(name):
    with open(DATA / name) as f:
        return [json.loads(l) for l in f]


@pytest.fixture(scope="module")
def corpus():
    dossiers = _load("signal_dossiers.jsonl")
    ev = SignalEvaluator(label_cache_path=CACHE if CACHE.exists() else None)
    ev.load_context(_load("accounts.jsonl"), _load("owners.jsonl"), _load("telemetry.jsonl"), _load("artifacts.jsonl"), dossiers)
    by_id = {d["signal_id"]: d for d in dossiers}
    return ev, by_id


def run(corpus, sid):
    ev, by_id = corpus
    return ev.evaluate(by_id[sid])


def rules(r):
    return {v["rule"] for v in r["violations"]}


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def needs_labels(corpus):
    if not corpus[0].cx.classifier_active:
        pytest.skip("NLI classifier not available; label-dependent expectations skipped")


# ─────────────────────────────────────────────────────────────────────────────
def test_sig_0001_fabricated_quote_skipped_enrichment_real_drop(corpus):
    """Raw facts: lifecycle s3 hypothesis_formed→scored ('fast-tracked'); score_signal rides that edge;
    art_00620 text says '90M rows', quote says '180M rows'; dau_seats 7d mean 127.9 → 89.9 = −29.6%
    (claim −35%, within 5.5pp); notify 00:28Z = 16:28 Los Angeles, 29.1h after open (P2 target 72h);
    email/en-US matches owner; 20,000 within [18,000, 75,000]; acknowledged by owner."""
    r = run(corpus, "sig_0001")
    assert rules(r) == {"TM", "I4", "I6"}
    assert only(r, "TM")[0]["step"] == 3 and "hypothesis_formed→scored" in only(r, "TM")[0]["explanation"]
    assert only(r, "I4")[0]["step"] == 3 and "score_signal" in only(r, "I4")[0]["explanation"]
    i6 = only(r, "I6")[0]
    assert i6["step"] == 2 and i6["severity"] == 1.0 and "art_00620" in i6["explanation"]
    f = r["_facts"]
    assert f["evidence"][0]["status"] == "fabricated"
    assert f["claim_status"] == ["grounded"]
    assert abs(f["claim_detail"][0]["corrected_pct"] - (-29.6)) < 0.5
    assert abs(f["claim_detail"][0]["raw_pct"] - (-39.7)) < 0.5
    assert f["has_customer_text"] is False and f["triggers"] == [] and f["reached_human"] is True
    assert f["days_to_renewal"] == 165


def test_sig_0005_restricted_quote_quoted_history_missing_day_artifact(corpus):
    """Raw facts: s3 hypothesis_formed→scored; art_02564 is restricted=True in the artefact record and quoted,
    human notified → P3 1.0; art_02546 is 'Sorted, thanks. Ignore the thread below…' with an 'On … wrote:'
    quoted section → stale; notify 15:33Z = 16:33 Berlin (in window) via email/en-US but owner is slack/de-DE
    → two P6; arr_at_risk 17,500 < floor 18,000 and routed → M4; dau_seats corrected −18.1% vs claim −30%,
    raw sum −29.8% with 02-23 missing → artifact; flags [pilot] → no P2; confidence low → no P5."""
    r = run(corpus, "sig_0005")
    assert {"TM", "I4", "P6", "M4", "P3", "M6"} <= rules(r)
    assert not {"P2", "P5", "P7", "I6", "P1"} & rules(r)
    assert len(only(r, "P6")) == 2
    assert only(r, "P3")[0]["severity"] == 1.0 and "art_02564" in only(r, "P3")[0]["explanation"]
    assert "17,500" in only(r, "M4")[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"]
    assert any("missing day" in e for e in f["claim_detail"][0]["events"])
    assert f["reached_human"] is True and f["triggers"] == []
    needs_labels(corpus)
    assert [e["status"] for e in f["evidence"]] == ["stale", "verified"]
    assert "Q4" in rules(r) and "art_02546" in only(r, "Q4")[0]["explanation"]
    assert f["has_customer_text"] is False and f["verified_sources"] == ["security_review"]
    assert "Q2" in rules(r)          # product_gap with no supporting text and only artefact claims


def test_sig_0013_legal_hold_with_customer_visible_play(corpus):
    """Raw facts: full happy-path lifecycle; account flags [legal_hold, named_exec_sponsor]; play
    reliability_rca_share, customer_visible True → P2; notify 00:01Z = 11:01 Sydney, 68.1h (P2 72h) → ok;
    slack/en-AU matches; 11,500 within [6,000, 42,000]; no metric claims; confidence low."""
    r = run(corpus, "sig_0013")
    assert rules(r) == {"P2"}
    assert only(r, "P2")[0]["severity"] == 1.0 and "legal_hold" in only(r, "P2")[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == [] and f["triggers"] == [] and f["reached_human"] is True
    assert f["days_to_renewal"] == 49


def test_sig_0009_human_preempt_high_confidence_single_source(corpus):
    """Raw facts: s3 hypothesis_formed→acknowledged with trigger human_preempt (allowed); no notifications;
    confidence high with evidence = crm_note ×2 (same artefact art_03493 attached twice) + one bot_alert
    → one distinct non-bot source → P5; duplicate attach → Q4; 375,500 within [60,000, 715,000];
    hypothesis champion_departure, evidence 'usage steady, champion happy' → unsupported."""
    r = run(corpus, "sig_0009")
    assert {"P5", "Q4"} <= rules(r)
    assert not {"TM", "I1", "I2", "I3", "I4", "T3", "P1"} & rules(r)
    assert "art_03493" in only(r, "Q4")[0]["explanation"] and "2 times" in only(r, "Q4")[0]["explanation"]
    f = r["_facts"]
    assert f["reached_human"] is True and f["final_state"] == "acknowledged" and f["triggers"] == []
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified", "verified"]
    assert f["verified_sources"] == ["crm_note"]
    needs_labels(corpus)
    assert any(v["rule"] == "Q2" and "champion_departure" in v["explanation"] for v in r["violations"])


def test_sig_0015_enrichment_timeout_then_routed_late(corpus):
    """Raw facts: s4 evidence_pending→scored with trigger enrichment_timeout (allowed), s5 scored→routed
    (required after timeout — satisfied); at s4 an action named notify_owner rides evidence_pending→scored
    → I4 (notify only on scored→routed); real notify 04:33Z = 12:33 Singapore, 204h after open vs P2 72h
    → T3 at full scale; slack/en-SG matches; 112,500 within [9,000, 287,500]; art_00234 is an internal
    'asked abt dark mode lol' note — casual, not stale customer evidence."""
    r = run(corpus, "sig_0015")
    assert rules(r) == {"I4", "T3"}
    assert only(r, "I4")[0]["step"] == 4 and "notify_owner" in only(r, "I4")[0]["explanation"]
    assert only(r, "T3")[0]["severity"] == 0.3
    f = r["_facts"]
    assert f["reached_human"] is True and f["triggers"] == [] and f["days_to_renewal"] == 34
    needs_labels(corpus)
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]


def test_sig_0278_legacy_double_count_artifact_and_backward_move(corpus):
    """Raw facts: s3 hypothesis_formed→candidate (backward, never allowed) → I1; s4 candidate→hypothesis_formed
    (not in matrix) → TM; notify 08:47Z = 10:47 Berlin, 91.3h vs P2 72h → T3 partial; routed with 2,500 <
    floor 6,000 → M4; legacy collector, api_calls claim −66% with window spanning 2026-05-18: corrected mean
    −21.8%, raw sum −66.5% → artifact named 'legacy'; customer chat 'team is on holiday' is real text."""
    r = run(corpus, "sig_0278")
    assert rules(r) == {"I1", "TM", "T3", "M4", "M6"}
    assert only(r, "I1")[0]["step"] == 3 and only(r, "TM")[0]["step"] == 4
    assert 0 < only(r, "T3")[0]["severity"] < 0.3
    assert "2,500" in only(r, "M4")[0]["explanation"]
    assert "legacy" in only(r, "M6")[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"] and f["triggers"] == []
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified", "verified"]
    assert f["has_customer_text"] is True


def test_sig_0420_ingest_gap_artifact(corpus):
    """Raw facts: apac account, dau_seats claim −30% with window 06-01..06-14 containing the 06-11→13 ingest
    gap (3 missing days); corrected mean on the 4 clean days is +22.1%, raw sum −30.2% → artifact named
    'ingest gap'; acknowledged with 14,500 < floor 18,000 → M4; confidence high with support_ticket +
    crm_note = 2 sources → no P5; notify 12:59 Singapore, 61.2h → ok."""
    r = run(corpus, "sig_0420")
    assert rules(r) == {"M4", "M6"}
    assert "ingest gap" in only(r, "M6")[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"] and f["claim_detail"][0]["missing_days"] == 3
    assert f["has_customer_text"] is True and f["triggers"] == []


def test_sig_0058_departure_notice_suppressed_after_timeout(corpus):
    """Raw facts: s4 evidence_pending→scored via enrichment_timeout, then s5 scored→suppressed — spec §4.5
    says a timed-out signal must still reach a human → TM; art_93328 not in corpus → I6 1.0; art_01647 is a
    customer email 'As of Friday I am no longer the point of contact here' (champion departure) with renewal
    60 days out → mandatory trigger; suppressed with no notification → P1 1.0; hypothesis benign_variation
    while a trigger is present → Q2; art_01637 asks for a credit — not a legal reference."""
    r = run(corpus, "sig_0058")
    assert {"I6", "TM"} <= rules(r)
    assert "art_93328" in only(r, "I6")[0]["explanation"] and only(r, "I6")[0]["severity"] == 1.0
    assert any("4.5" in v["explanation"] for v in only(r, "TM"))
    f = r["_facts"]
    assert f["reached_human"] is False and f["days_to_renewal"] == 60
    assert "M4" not in rules(r) and "P5" not in rules(r)
    needs_labels(corpus)
    assert f["triggers"] == ["buyer_or_champion_departure"]
    assert "P1" in rules(r) and only(r, "P1")[0]["severity"] == 1.0
    assert any(v["rule"] == "Q2" and "mandatory trigger" in v["explanation"] for v in r["violations"])


def test_sig_0111_cross_tenant_and_twelvefold_restatement(corpus):
    """Raw facts: at s5 an action named notify_owner rides evidence_received→scored → I4; notify 09:48Z =
    11:48 Madrid, 72.2h vs 72h → T3 tiny; routed with 16,000 < floor 18,000 → M4; restated '$192,000' =
    12 × 16,000 → M5; art_00849 belongs to acct_0039 → P4; dau_seats corrected −28.7%, raw −38.9%, claim −48%
    → neither reproduces → 'wrong'; art_03224 is a credit memo *resolving* a dispute — not a billing trigger;
    none of the four same-account artefacts quote older text."""
    r = run(corpus, "sig_0111")
    assert rules(r) == {"I4", "T3", "M4", "M5", "P4", "M6"}
    assert only(r, "I4")[0]["step"] == 5
    assert "12×" in only(r, "M5")[0]["explanation"]
    assert "art_00849" in only(r, "P4")[0]["explanation"] and "acct_0039" in only(r, "P4")[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["wrong"]
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified", "verified", "verified", "other_account"]
    assert f["triggers"] == [] and f["has_customer_text"] is True


def test_sig_0208_out_of_window_renotify_altered_quote(corpus):
    """Raw facts: s3 hypothesis_formed→scored → TM + I4; notifications 09:41Z = 05:41 New York (outside
    08–19, severity P3 not exempt) → T1, then 15:25Z = 11:25 with a 5.7h gap → T2; acknowledged with 2,500 <
    floor 6,000 → M4; restated '$30,000' = 12 × 2,500 → M5; quote says '4 wks', artefact says '2 wks' → I6;
    dau_seats corrected −18.8%, raw −30.4% with one missing day → artifact; hypothesis no_hypothesis resting
    only on that artefact claim → Q2."""
    r = run(corpus, "sig_0208")
    assert rules(r) == {"TM", "I4", "T1", "T2", "M4", "M5", "I6", "M6", "Q2"}
    assert only(r, "T1")[0]["step"] == 4 and "05:41" in only(r, "T1")[0]["explanation"]
    assert "5.7h" in only(r, "T2")[0]["explanation"]
    assert "12×" in only(r, "M5")[0]["explanation"]
    assert only(r, "I6")[0]["severity"] == 1.0
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"] and f["evidence"][0]["status"] == "fabricated"
    assert f["has_customer_text"] is False and f["reached_human"] is True
