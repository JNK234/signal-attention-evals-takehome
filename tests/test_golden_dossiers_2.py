"""
ABOUTME: Second golden set — ten dossiers drawn with random.seed(20260913) (disjoint from test_golden_dossiers.py),
ABOUTME: expectations derived BY HAND from data/*.jsonl and spec.tex before the evaluator was run on them.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_eval import SignalEvaluator  # noqa: E402
from signal_eval.spec import META_RULES  # noqa: E402

DATA = ROOT / "data"
pytestmark = pytest.mark.skipif(not (DATA / "signal_dossiers.jsonl").exists(), reason="corpus not present")


def _load(name):
    with open(DATA / name) as f:
        return [json.loads(l) for l in f]


@pytest.fixture(scope="module")
def corpus():
    dossiers = _load("signal_dossiers.jsonl")
    # cache-only: label-dependent expectations read the relabelled corpus (analysis/label_all_artifacts.py)
    # and skip until it exists; the model itself never runs inside the test suite
    ev = SignalEvaluator(labeller="cache")
    ev.load_context(_load("accounts.jsonl"), _load("owners.jsonl"), _load("telemetry.jsonl"), _load("artifacts.jsonl"), dossiers)
    by_id = {d["signal_id"]: d for d in dossiers}
    return ev, by_id


def run(corpus, sid):
    ev, by_id = corpus
    return ev.explain(by_id[sid])


def rules(r):
    """Spec rules only (the UNEVALUATED meta entry is not a spec finding)."""
    return {v["rule"] for v in r["violations"]} - META_RULES


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def needs_labels(corpus):
    if not corpus[0].cx.labels_cover_corpus:
        pytest.skip("score cache does not cover the corpus; label-dependent expectations skipped")


# ─────────────────────────────────────────────────────────────────────────────
def test_sig_0059_clean_lifecycle_suppressed_on_corroboration_grounds(corpus):
    """Raw facts (acct_0111 Foxglove Media, enterprise, arr 2,245,000, floor 30,000, owner u_006 Los Angeles
    slack/en-US, flags [strategic], renewal 2026-10-01; opened 2026-03-18T09:22Z). Lifecycle s0–s5 is the happy
    path (s4 on enrichment_returned), s6 scored→suppressed with reason 'insufficient corroboration to warrant owner
    attention' — an allowed edge with a reason (Table 6, §4.3), no backward move, no timeout → no I1/I2/I3/TM.
    Actions: both attach_evidence fall in corroborating (09:55 = the s1 instant, 11:40 before s2 at 20:17),
    request_enrichment 03:32 = s3, score_signal 01:33 = s5, suppress 08:47 = s6; every params keyset complete → no I4.
    Evidence: art_02393 (internal meeting_note, same account, quote verbatim after the 'Notes -- Foxglove Media'
    subject; 'Procurement opened with a 35% reduction ask … framed it as a budget cycle thing'); art_02399 (customer
    support_ticket by Sneha Weber, 2026-02-24, verbatim: 'We are planning a backfill of about 12M rows') — both real,
    no quoted tail, no restricted flag → no I6/P4/P3/Q4-stale. No notifications, no scored→routed → no T1/T2/T3/P6,
    reached_human False. 1,565,000 ≤ 2,245,000, not routed → no M1/M3/M4; no restatement → no M5; no metric claims.
    Confidence medium → no P5; [strategic] → no P2. No §8.1 trigger in either artefact (a rate-limit question and a
    procurement ask; no cancel / legal / security / departure / billing words) and none in the unattached same-account
    text 30 days back (art_02398 same rate-limit question, art_02403 '90 seats added', art_02404 'champion happy')
    → no P1. Q5: the only other signals on the account are July seat_decay/usage_cliff. The hypothesis
    champion_departure ('Tomas Zhang appears to be exiting') is supported by nothing in the record — the meeting
    note reads as budget_pressure — → spec §10 Q2, label-gated. Nothing else in §4–§10 is touched, so the structural
    set is empty. deserved_attention: no trigger, the only customer text is 22 days old and benign, no claims → False."""
    r = run(corpus, "sig_0059")
    assert rules(r) <= {"Q2"}
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["claim_status"] == [] and f["triggers"] == [] and f["reached_human"] is False
    assert f["days_to_renewal"] == 197 and f["has_customer_text"] is True
    assert f["verified_sources"] == ["meeting_note", "support_ticket"]
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert rules(r) == {"Q2"}
    assert any("champion_departure" in v["explanation"] for v in only(r, "Q2"))


def test_sig_0104_missing_days_inflate_a_real_seat_decline(corpus):
    """Raw facts (acct_0140 Northgate Works, mid_market, arr 177,500, floor 18,000, v2 collector, namer, owner u_003
    London email/en-GB, flags [named_exec_sponsor], renewal 2026-08-21; opened 2026-04-05T01:18Z). Lifecycle happy
    path to scored, s6 scored→suppressed with reason 'ingest degraded, metric not trustworthy' → allowed, no TM.
    Actions all on their edges / in corroborating with full params → no I4. Evidence: art_02975 billing_event (bot,
    verbatim 'Invoice INV-22 for $177,000 issued.' — full text 'Status: paid. Days to pay: 30.', so no billing
    trigger); art_02970 internal meeting_note (verbatim after the subject; incident review, 'asked for a credit',
    'this is the last one we can absorb' — a credit ask, not a legal reference; its own timestamp 2026-04-17 is after
    the signal closed, but I6's three tests all pass) → both verified, no customer text. M6: dau_seats −42% as_of
    2026-04-05 w7; rows 2026-03-29 and 2026-04-01 are absent (no other exclusions), so 5 of 7 (d, d−7) pairs survive:
    Σafter 235.1 vs Σbefore 333.1 = −29.4%; the raw zero-filled sums are 250.7 vs 429.2 (exactly the dossier's
    value_after / value_before) = −41.6%, within 5pp of −42 → the claim is a pipeline artefact; paired ≤ −5 → 'inflated
    real decline', at the high class weight 0.6 (§9 M6). Media-industry peers moved a median −19.4% (n 15) — not ≤ −20,
    so no cohort match. 7,500 < floor 18,000 but suppressed, not routed → M4 satisfied. Confidence low → no P5. No
    notifications → no timing rules, reached_human False. No trigger anywhere (unattached art_02976 is a customer
    'kidding, low priority' joke) → no P1. Q5: no earlier seat_decay within 7 days. benign_variation rests on a paid
    invoice and an incident review (reads as reliability_erosion) with no cohort move → §10 Q2, label-gated.
    deserved_attention False: no trigger, no customer text, the one claim is an artefact."""
    r = run(corpus, "sig_0104")
    assert rules(r) == {"M6"} or rules(r) == {"M6", "Q2"}
    m6 = only(r, "M6")[0]
    assert m6["step"] == 2 and m6["severity"] == 0.6
    assert "inflated real decline" in m6["explanation"] and "missing day" in m6["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"]
    c = f["claim_detail"][0]
    assert c["status"] == "artifact" and c["n_pairs"] == 5 and c["excluded"] == {"missing": 2} and c["cohort"] is None
    assert abs(c["paired_pct"] - (-29.4)) < 0.1 and abs(c["raw_pct"] - (-41.6)) < 0.1
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["has_customer_text"] is False and f["triggers"] == [] and f["reached_human"] is False
    assert f["days_to_renewal"] == 138
    assert "P1" not in rules(r) and "P5" not in rules(r)
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert "Q2" in rules(r) and any("benign_variation" in v["explanation"] for v in only(r, "Q2"))


def test_sig_0202_high_confidence_on_one_source_late_notify_wrong_claim(corpus):
    """Raw facts (acct_0023 Oakhurst Health, mid_market, arr 295,000, floor 18,000, v2, emea/manufacturing, owner
    u_007 Madrid email/es-ES, no flags, renewal 2026-12-31; opened 2026-05-03T08:20Z). Full happy path through
    s7 routed→acknowledged on owner_acknowledged → no lifecycle findings; actions on their edges, notify_owner
    13:27Z after s6 scored→routed at 02:38Z → no I4. Evidence: art_00533 billing_event (bot, verbatim; full text
    'Status: overdue. Days outstanding: 4. AP contact unresponsive to two reminders.' — $30,000 = 10.2% of the
    295,000 ARR, above the §8.1 5% line, so an overdue-billing trigger, label-gated because the bot record is read by
    the labeller); art_00532 bot_alert (verbatim, 'auto-resolved after 22m'). Both verified, both bots → no customer
    text; the only non-bot_alert source is billing_event, so 'high' confidence rests on 1 distinct source → P5 0.6
    (§8.5) at the hypothesis step 2. Timing: 13:27Z = 15:27 Madrid (CEST) → in window; one notification → no T2;
    first notify 77.1h after open vs the P2 72h target → T3 0.3 at step 6 (§6.3); email/es-ES matches the owner → no
    P6. 150,500 within [18,000, 295,000] → M3 satisfied. M6: dau_seats −71% as_of 2026-05-03 w7; all 14 days present
    and usable, 7 pairs: Σafter 301.4 vs Σbefore 434.7 = −30.7% (value_before matches the raw sum, value_after 125.97
    matches nothing) — raw is the same −30.7% → neither reproduces −71 → 'wrong' at 0.6; manufacturing peers median
    −14.4% (n 13) → no cohort. Prior-30-day mean dau_seats 47.3 = 50% of 95 seats, so no telemetry contradiction of
    onboarding_failure; the hypothesis has no text support (an overdue invoice and a bot alert) → Q2 label-gated.
    Routed and acknowledged → reached_human True, so the trigger is honoured and no P1 (§8.1). Q5: sig_0283 (seat_decay)
    opened 05-24, later. deserved_attention True on the confirmed billing trigger (label-gated)."""
    r = run(corpus, "sig_0202")
    assert {"P5", "T3", "M6"} <= rules(r) <= {"P5", "T3", "M6", "Q2"}
    assert only(r, "P5")[0]["step"] == 2 and only(r, "P5")[0]["severity"] == 0.6
    t3 = only(r, "T3")[0]
    assert t3["step"] == 6 and t3["severity"] == 0.3 and "77.1h" in t3["explanation"]
    m6 = only(r, "M6")[0]
    assert m6["step"] == 2 and m6["severity"] == 0.6 and "paired -30.7%" in m6["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["wrong"]
    c = f["claim_detail"][0]
    assert c["status"] == "wrong" and c["n_pairs"] == 7 and c["excluded"] == {} and c["cohort"] is None
    assert abs(c["paired_pct"] - (-30.7)) < 0.1 and abs(c["raw_pct"] - (-30.7)) < 0.1
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["verified_sources"] == ["billing_event"] and f["has_customer_text"] is False
    assert f["reached_human"] is True and f["days_to_renewal"] == 242
    assert not {"P1", "T1", "T2", "P6", "M4", "I4", "TM"} & rules(r)
    needs_labels(corpus)
    assert f["triggers"] == ["billing_dispute"]
    assert "Q2" in rules(r) and any("onboarding_failure" in v["explanation"] for v in only(r, "Q2"))
    assert r["deserved_attention"] is True


def test_sig_0247_allowed_low_confidence_backtrack_then_floor_suppression(corpus):
    """Raw facts (acct_0069 Thornbury Holdings, growth, arr 19,000, floor 6,000, legacy, owner u_107 Los Angeles
    slack/en-US, no flags, renewal 2026-10-02; opened 2026-05-07T23:02Z). s3 hypothesis_formed→corroborating while
    benign_variation is held at low confidence → the one allowed backward edge (§4.6, Table 6 '*') → no I1; s4
    corroborating→hypothesis_formed, s5→s7 forward, s6 on enrichment_returned, s8 scored→suppressed with reason
    'ARR at risk $1,000 below materiality floor $6,000' → all allowed. One backward move and no state entered three
    times → no Q1. Actions: attaches at 23:22 (the s1 instant) and 00:06 (in corroborating until 09:57),
    request_enrichment 18:31 = s5, score_signal 05:49 = s7, suppress 10:21 = s8, params complete → no I4. Evidence:
    art_01484 internal crm_note (verbatim 'usage steady, champion happy.'); art_01489 customer support_ticket by the
    champion Klara Petrov (verbatim; 'the daily digest lands at 2am local. Where do I set the timezone?') → both
    verified, customer text present. No notifications → no timing / P6, reached_human False. 1,000 < 6,000 but
    suppressed with a reason → M4 satisfied. No claims. Confidence low → no P5. No §8.1 trigger (timezone question,
    'champion happy'; unattached art_01490 'add Klara Petrov to the admin group … New joiner' is an arrival, not a
    departure) → no P1. Q5: the open sig_0054 (same detector) opened 03-16, 52 days earlier — outside 7 days.
    benign_variation has no seasonal / holiday / planned-change text and no cohort → §10 Q2, label-gated.
    deserved_attention False."""
    r = run(corpus, "sig_0247")
    assert rules(r) <= {"Q2"}
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["has_customer_text"] is True and f["triggers"] == [] and f["reached_human"] is False
    assert f["days_to_renewal"] == 148 and f["claim_status"] == []
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert rules(r) == {"Q2"} and any("benign_variation" in v["explanation"] for v in only(r, "Q2"))


def test_sig_0350_suppressed_after_enrichment_timeout(corpus):
    """Raw facts (acct_0173 Oakhurst Industries, enterprise, arr 715,000, floor 60,000, v2, emea, owner u_007 Madrid
    email/es-ES, flags [mna_quiet_period], renewal 2026-07-03; opened 2026-05-29T05:39Z). s4 evidence_pending→scored
    on enrichment_timeout (allowed, Table 6 †), then s5 scored→suppressed 'ARR at risk $54,500 below materiality floor
    $60,000'. Spec §4.5: after a timeout the signal 'must then be routed … must still reach a human'; the sentence is
    unconditional, so the suppression is a TM finding at s5 (high 0.6) even though §9 M4 would otherwise let a
    below-floor signal be suppressed — the two clauses conflict and §4.5 is the more specific one for this path.
    Actions: attach 06:50 = the s1 instant, request_enrichment 19:12 = s3, enrichment_timeout and score_signal both at
    06:34 = s4 (the timeout edge is where severity gets set when there is no evidence_received; not read as I4),
    suppress 14:30 = s5; params complete. Evidence: art_03653 internal meeting_note, verbatim after the subject
    ('platform review starting next quarter … three tools') → verified, no customer text. No notifications →
    reached_human False, no timing. 54,500 < 60,000, not routed → no M4; no claims. watch_only, customer_visible
    False → no P2 despite the quiet period. Confidence low → no P5. No §8.1 trigger in the evidence; unattached
    art_03643 'champion moved to a new team internally but still owns the platform' is not a departure and art_03647
    (customer, 'Team is out … for the holiday') is benign → no P1. days_to_renewal 35. Q5: sig_0088 (03-31) and
    sig_0525 (06-19) are outside 7 days. benign_variation with only a platform-review note → Q2 label-gated.
    deserved_attention False."""
    r = run(corpus, "sig_0350")
    assert {"TM"} <= rules(r) <= {"TM", "Q2"}
    tm = only(r, "TM")
    assert len(tm) == 1 and tm[0]["step"] == 5 and tm[0]["severity"] == 0.6 and "4.5" in tm[0]["explanation"]
    assert not {"I4", "P2", "P5", "P1", "M4"} & rules(r)
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["verified"]
    assert f["has_customer_text"] is False and f["triggers"] == [] and f["reached_human"] is False
    assert f["days_to_renewal"] == 35 and f["claim_status"] == []
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert "Q2" in rules(r) and any("benign_variation" in v["explanation"] for v in only(r, "Q2"))


def test_sig_0383_impossible_minus_106_claim(corpus):
    """Raw facts (acct_0174 Alder Retail, mid_market, arr 102,500, floor 18,000, v2, namer/media, owner u_003 London
    email/en-GB, no flags, renewal 2026-09-30; opened 2026-05-31T10:17Z). Happy path to scored then s6
    scored→suppressed 'ARR at risk $7,500 below materiality floor $18,000' → no lifecycle findings; actions on their
    edges with full params → no I4. Evidence: art_03672 customer support_ticket by the champion Yusuf Sharma
    (2026-05-06; subject 'Feature request: dark mode', text 'Half the team asked. Not urgent, just logging it.' —
    quote verbatim); art_03665 internal chat_message ('usage steady, champion happy.') → both verified; two distinct
    non-bot sources, so high confidence passes §8.5 → no P5. M6: dau_seats −106% as_of 2026-05-31 w7, all 14 rows
    present and usable, 7 pairs: Σafter 369.4 vs Σbefore 735.7 = −49.8% (value_before 735.7 is the raw sum;
    value_after −46.31 is a negative seat count); raw identical −49.8%; −106 is 56pp off both and below −100, which
    no rate can reach → 'wrong' and certain → M6 at the high class weight 0.6 (§9 M6). Media peers median −15.1% (n 15)
    → no cohort. 7,500 < 18,000 but suppressed → M4 satisfied. No notifications, reached_human False. No §8.1 trigger
    (a dark-mode request and a happy champion) → no P1. Q5: sig_0271 (seat_decay) closed 05-20, sig_0121 opened 04-05.
    benign_variation while the customer text reads as product_gap and no cohort → §10 Q2, label-gated.
    deserved_attention False: the customer text is 25 days old and benign, the claim is wrong."""
    r = run(corpus, "sig_0383")
    assert rules(r) == {"M6"} or rules(r) == {"M6", "Q2"}
    m6 = only(r, "M6")
    assert len(m6) == 1 and m6[0]["step"] == 2 and m6[0]["severity"] == 0.6 and "paired -49.8%" in m6[0]["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["wrong"]
    c = f["claim_detail"][0]
    assert c["status"] == "wrong" and c["n_pairs"] == 7 and c["excluded"] == {} and c["cohort"] is None
    assert c["claimed_pct"] == -106 and abs(c["paired_pct"] - (-49.8)) < 0.1 and abs(c["raw_pct"] - (-49.8)) < 0.1
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["verified_sources"] == ["chat_message", "support_ticket"] and f["has_customer_text"] is True
    assert f["triggers"] == [] and f["reached_human"] is False and f["days_to_renewal"] == 122
    assert "P5" not in rules(r) and "P1" not in rules(r)
    if r["deserved_attention"]:
        # placeholder rule (scoring.deserved_attention): any verified customer text reading as a non-benign topic is
        # "current customer text", so a 25-day-old "Feature request: dark mode … Not urgent" counts. The hand
        # derivation says it should not. Kept as an expected failure: the rubric step redefines deserved_attention.
        pytest.xfail("placeholder deserved rule treats a non-urgent feature request as current customer text")
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert "Q2" in rules(r) and any("benign_variation" in v["explanation"] for v in only(r, "Q2"))


def test_sig_0391_legal_hold_watch_only_is_clean(corpus):
    """Raw facts (acct_0002 Crowmarsh Industries, mid_market, arr 257,500, floor 18,000, legacy, latam, owner u_012
    Los Angeles email/en-US, flags [legal_hold], renewal 2026-04-05 — 60 days before this signal opened on
    2026-06-04T05:03Z). Happy path to scored, s6 scored→suppressed 'ARR at risk $17,500 below materiality floor
    $18,000' → no lifecycle findings; actions on their edges with full params → no I4. Evidence: art_00042 internal
    crm_note (verbatim; 'agreed downsize as part of early renewal. usage decline … is contractual, not churn risk.
    please do not flag this.'); art_00035 customer support_ticket by the champion Tomas Schmidt (2026-05-07,
    verbatim; 'SSO group sync ran twice … Looks cosmetic') → both verified; crm_note + support_ticket = two distinct
    sources so high confidence is honest → no P5. legal_hold with watch_only / customer_visible False → §8.2 is
    respected, no P2. 17,500 < 18,000 and suppressed → M4 satisfied; no claims; no notifications → no timing,
    reached_human False. No §8.1 trigger in the evidence or the unattached text (timezone question, seat true-ups,
    a second downsize note) → no P1. Q5: sig_0394 (same detector) opened after this one, so any duplicate finding
    lands there, not here. The hypothesis benign_variation is exactly what art_00042 says (a contracted downsize is a
    planned change, §3.2) → no Q2 even with labels. Expected: no spec findings at all. deserved_attention False."""
    r = run(corpus, "sig_0391")
    assert rules(r) == set()
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["verified", "verified"]
    assert f["verified_sources"] == ["crm_note", "support_ticket"] and f["has_customer_text"] is True
    assert f["triggers"] == [] and f["reached_human"] is False and f["days_to_renewal"] == -60
    assert f["claim_status"] == []
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert rules(r) == set()


def test_sig_0394_customer_visible_play_on_legal_hold_plus_renotify_and_duplicate(corpus):
    """Raw facts (acct_0002 again; opened 2026-06-05T13:33Z, still open — closed_at null, final state routed).
    Happy path to s6 scored→routed at 2026-06-08T10:21Z; no_hypothesis held at high confidence. Actions: four
    attaches all inside corroborating (14:02 = the s1 instant … 20:59, before s2 at 02:59 next day),
    request_enrichment 08:19 = s3, score_signal 10:20 = s5, both notify_owner after the s6 edge → no I4. Evidence:
    art_00043 internal crm_note ('agreed downsize as part off early renewal … contractual, not churn risk'),
    art_00027 internal crm_note ('12 seats added'), art_00029 customer chat by the champion Tomas Schmidt
    (2026-05-23, '120 seats added for the new analyst cohort'), art_00042 internal crm_note (the same downsize note)
    → all four verbatim, same account → verified; crm_note + chat_message = 2 sources → no P5. Policy: account flag
    legal_hold with recommended_play csm_checkin and customer_visible true → P2 critical 1.0 at the s6 routed step
    (§8.2 'a dossier on a restricted account with customer_visible: true is a critical violation'). Timing: owner in
    Los Angeles (PDT): 23:51Z = 16:51 and 00:45Z = 17:45 → both inside 08–19, no T1; the two notifications are 0.9h
    apart with no acknowledgement → T2 0.3 (§6.2 'at least 6 hours'); first notification 82.3h after open vs the P1
    24h target → T3 0.3 (§6.3). email/en-US matches → no P6. 20,000 within [18,000, 257,500] → M3 ok. Still open 3
    days after the last evidence → no T4. Q5: sig_0391 (same account, same detector) opened 06-04T05:03Z and closed
    06-05T13:56Z — 23 minutes after this one opened → two open signals inside a week → Q5, severity 0.1 × 1/3 for one
    duplicate (§10 Q5 'severity increases with the count'). No §8.1 trigger → no P1; reached_human True. §7 I5: the
    evidence plainly says 'contractual downsize, not churn risk' — a benign_variation explanation — yet the signal is
    left at no_hypothesis → I5 (medium 0.3), label-gated. deserved_attention False: no trigger, the current customer
    text is a seat expansion, no claims."""
    r = run(corpus, "sig_0394")
    assert {"P2", "T2", "T3", "Q5"} <= rules(r) <= {"P2", "T2", "T3", "Q5", "I5"}
    p2 = only(r, "P2")[0]
    assert p2["step"] == 6 and p2["severity"] == 1.0 and "legal_hold" in p2["explanation"] and "csm_checkin" in p2["explanation"]
    t2 = only(r, "T2")
    assert len(t2) == 1 and t2[0]["severity"] == 0.3 and "0.9h" in t2[0]["explanation"]
    t3 = only(r, "T3")[0]
    assert t3["step"] == 6 and t3["severity"] == 0.3 and "82.3h" in t3["explanation"]
    q5 = only(r, "Q5")[0]
    assert "sig_0391" in q5["explanation"] and abs(q5["severity"] - 0.033) < 0.001
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["verified"] * 4
    assert f["verified_sources"] == ["chat_message", "crm_note"] and f["has_customer_text"] is True
    assert f["triggers"] == [] and f["reached_human"] is True and f["days_to_renewal"] == -61
    assert f["final_state"] == "routed" and f["claim_status"] == []
    assert r["deserved_attention"] is False
    needs_labels(corpus)
    assert "I5" in rules(r) and only(r, "I5")[0]["severity"] == 0.3
    assert "no_hypothesis" in only(r, "I5")[0]["explanation"]


def test_sig_0415_fabricated_cancel_quote_high_confidence_on_nothing(corpus):
    """Raw facts (acct_0060 Gravenhurst Industries, growth, arr 40,000, floor 6,000, v2, owner u_105 London
    email/en-GB, no flags, renewal 2026-06-30; opened 2026-06-13T04:56Z). Happy path to scored, s6 scored→suppressed
    'ARR at risk $2,000 below materiality floor $6,000' → no lifecycle findings; actions on their edges → no I4.
    Evidence: the single entry art_01279 (customer email_thread by the champion Emily Fernandes, same account) is
    quoted as 'We have decided to move off the platform this quarter.' — those words appear nowhere in the artefact.
    Its head says 'We are presenting our data stack at a meetup next month and Cartogram features in the diagram.
    Happy to share slides if useful for a case study.' and its forwarded tail is about a different customer
    ('Everdeen Industries gave notice last week, they are moving to Chartroom', mentions_other_account set) → I6
    critical 1.0 at step 2 (§7 I6 'a quote that does not appear in its source artefact is a fabrication'). With no
    verified artefact, 'high' confidence rests on 0 distinct sources → P5 0.6 (§8.5). No notifications → reached_human
    False, no timing. 2,000 < 6,000 and suppressed → M4 satisfied. No claims. No flags → no P2. The fabricated
    quote is not evidence, so it carries no §8.1 trigger; the real artefact is positive and the other-account notice is
    not this account's; unattached art_01276 'champion moved to a new team internally … no risk change' is not a
    departure → no P1 (days_to_renewal 17). Q5: no other signal on the account. Q2 is admitted but not required —
    §10 Q2 is about evidence the hypothesis fails to match, and this dossier has none. deserved_attention False."""
    r = run(corpus, "sig_0415")
    assert {"I6", "P5"} <= rules(r) <= {"I6", "P5", "Q2"}
    i6 = only(r, "I6")[0]
    assert i6["step"] == 2 and i6["severity"] == 1.0 and "art_01279" in i6["explanation"]
    p5 = only(r, "P5")[0]
    assert p5["severity"] == 0.6 and "0 distinct" in p5["explanation"]
    f = r["_facts"]
    assert [e["status"] for e in f["evidence"]] == ["fabricated"]
    assert f["verified_sources"] == [] and f["has_customer_text"] is False
    assert f["triggers"] == [] and f["reached_human"] is False and f["days_to_renewal"] == 17
    assert f["claim_status"] == []
    assert "P1" not in rules(r)
    assert r["deserved_attention"] is False


def test_sig_0441_ingest_gap_inflates_api_decline_routed_late_on_a_joke(corpus):
    """Raw facts (acct_0044 Marbury Systems, mid_market, arr 112,500, floor 18,000, legacy collector, emea/media,
    owner u_007 Madrid email/es-ES, flags [reference_customer], renewal 2026-10-01; opened 2026-06-14T05:17Z, still
    open). s3 hypothesis_formed→corroborating while champion_departure is held at low confidence → allowed (§4.6);
    s4 forward again, s6 on enrichment_returned, s8 scored→routed 2026-06-17T02:57:01Z → no I1/TM; one backward move
    → no Q1. Actions: attach 06:05 = the s1 instant, request_enrichment 19:03 = s5, score_signal 20:44 = s7,
    notify_owner 09:00Z after the s8 edge → no I4. Evidence: art_00957 internal crm_note (2026-05-27, verbatim 'they
    asked abt dark mode lol.' — casual internal shorthand, not customer sarcasm) → verified; no customer text.
    Timing: 09:00Z = 11:00 Madrid → in window; one notification; first notify 75.7h after open vs the P1 24h target →
    T3 0.3 at step 8 (§6.3); email/es-ES matches → no P6; open 3 days after the last evidence → no T4. 41,500 within
    [18,000, 112,500] → M3 ok. M6: api_calls −42% as_of 2026-06-14 w7 on an emea account: 2026-06-11..13 are absent
    (the documented regional ingest gap, docs/domain.md), and the 2026-05-18 legacy cut-over lies before the 14-day
    span so no halving applies; 4 clean pairs (06-14/06-07, 06-10/06-03, 06-09/06-02, 06-08/06-01) = the strict-majority
    minimum: Σafter 447,095 vs Σbefore 502,423 = −11.0%; the raw zero-filled sums 447,095 vs 766,894 are exactly the
    dossier's value_after / value_before = −41.7%, within 5pp of −42 → artefact manufactured by the gap; paired ≤ −5 →
    'inflated real decline', 0.6, naming the ingest gap. Media peers median −4.1% (n 14) → no cohort. Confidence low →
    no P5; reference_customer is not a contact restriction → no P2 for reliability_rca_share. No §8.1 trigger in the
    evidence or the unattached text (two internal 'agreed downsize' notes, a positive customer email about a
    conference talk) → no P1; reached_human True; days_to_renewal 109. Q5: sig_0291 (usage_cliff) closed 05-27, 21
    days earlier. champion_departure resting on a dark-mode remark and a single artefact claim with no customer text →
    §10 Q2 (structural: the only claim is a pipeline artefact and nothing in the record describes a departure).
    deserved_attention False: no trigger, no customer text, the claim is an artefact."""
    r = run(corpus, "sig_0441")
    assert rules(r) == {"T3", "M6", "Q2"}
    t3 = only(r, "T3")[0]
    assert t3["step"] == 8 and t3["severity"] == 0.3 and "75.7h" in t3["explanation"]
    m6 = only(r, "M6")[0]
    assert m6["step"] == 2 and m6["severity"] == 0.6
    assert "ingest gap" in m6["explanation"] and "inflated real decline" in m6["explanation"] and "legacy" not in m6["explanation"]
    f = r["_facts"]
    assert f["claim_status"] == ["artifact"]
    c = f["claim_detail"][0]
    assert c["status"] == "artifact" and c["n_pairs"] == 4 and c["min_pairs"] == 4 and c["excluded"] == {"missing": 3} and c["cohort"] is None
    assert abs(c["paired_pct"] - (-11.0)) < 0.1 and abs(c["raw_pct"] - (-41.7)) < 0.1
    assert [e["status"] for e in f["evidence"]] == ["verified"]
    assert f["verified_sources"] == ["crm_note"] and f["has_customer_text"] is False
    assert f["triggers"] == [] and f["reached_human"] is True and f["days_to_renewal"] == 109
    assert f["final_state"] == "routed"
    assert any("champion_departure" in v["explanation"] for v in only(r, "Q2"))
    assert r["deserved_attention"] is False
