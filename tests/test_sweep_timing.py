"""
ABOUTME: Adversarial sweep of the timing rules (spec §6 T1–T4, §8.6 P6): exact boundaries (19:00 local, 6h, 3
ABOUTME: notifications, TTA target, 14 idle days), DST, offsets and naive timestamps, cold-path owner context.
"""

import pytest

from conftest import SignalEvaluator, happy_dossier, rules
from signal_eval.spec import SEV_WEIGHT, UNCERTAIN_FACTOR


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def _notify_at(d, *ats):
    """Replace the notification list (and matching notify actions stay at step 6, trailing the routed edge)."""
    d["notifications"] = [dict(d["notifications"][0], at=t, attempt=i + 1) for i, t in enumerate(ats)]
    d["actions"] = d["actions"][:3] + [dict(d["actions"][3], at=t) for t in ats]
    return d


# ── T1: owner-local window, boundaries. Owner is Europe/Berlin; 2026-03-02 is CET (UTC+1). ─────────

@pytest.mark.parametrize("at_utc,local,expect_t1", [
    ("2026-03-02T18:00:00Z", "19:00", True),    # exactly 19:00 local — the code reads the window as [08:00, 19:00)
    ("2026-03-02T17:59:59Z", "18:59", False),
    ("2026-03-02T07:00:00Z", "08:00", False),   # exactly 08:00 local
    ("2026-03-02T06:59:59Z", "07:59", True),
])
def test_T1_window_boundaries_owner_local(ev, at_utc, local, expect_t1):
    """spec §6.1 'No owner notifications outside 08:00–19:00 in the owner's own timezone.' The spec does not say
    whether 19:00:00 itself is inside; spec.py documents the half-open reading [08:00, 19:00). 08:00 is inside on
    either reading. Notifications here trail the routed edge (14:00Z) or precede it; only T1 is asserted."""
    d = _notify_at(happy_dossier(), at_utc)
    t1 = only(ev.evaluate(d), "T1")
    assert bool(t1) is expect_t1, (at_utc, local)
    if expect_t1:
        assert local in t1[0]["explanation"] and t1[0]["severity"] == SEV_WEIGHT["medium"]


def test_T1_uses_owner_local_clock_across_DST(ev):
    """spec §6.1: the window is the owner's clock. Berlin switches to CEST on 2026-03-29, so 17:30Z is 19:30 local
    on 2026-03-30 (T1) while the same 17:30Z on 2026-03-02 is 18:30 CET (inside). Only T1 is asserted (the late
    date also trips T3, which is correct and not under test here)."""
    d = _notify_at(happy_dossier(), "2026-03-30T17:30:00Z")
    d["lifecycle"][-1]["at"] = "2026-03-31T12:00:00Z"
    d["closed_at"] = "2026-03-31T12:00:00Z"
    t1 = only(ev.evaluate(d), "T1")
    assert len(t1) == 1 and "19:30" in t1[0]["explanation"]
    assert "T1" not in rules(ev.evaluate(_notify_at(happy_dossier(), "2026-03-02T17:30:00Z")))


def test_T1_with_positive_offset_timestamp(ev):
    """Timestamps may carry any ISO offset. 2026-03-03T00:00:00+05:30 is 18:30Z = 19:30 Berlin → T1."""
    d = _notify_at(happy_dossier(), "2026-03-03T00:00:00+05:30")
    t1 = only(ev.evaluate(d), "T1")
    assert len(t1) == 1 and "19:30" in t1[0]["explanation"]


def test_T1_with_naive_timestamp_read_as_UTC(ev):
    """A naive '2026-03-02T18:30:00' is read as UTC (util.ts) → 19:30 Berlin → T1, not a silently skipped rule."""
    d = _notify_at(happy_dossier(), "2026-03-02T18:30:00")
    t1 = only(ev.evaluate(d), "T1")
    assert len(t1) == 1 and "19:30" in t1[0]["explanation"]


def test_T1_cold_path_uses_metadata_owner_timezone():
    """Without load_context the owner's clock comes from metadata.owner_timezone (Europe/Berlin) → 18:30Z is T1."""
    d = _notify_at(happy_dossier(), "2026-03-02T18:30:00Z")
    r = SignalEvaluator(labeller=None).evaluate(d)
    assert len(only(r, "T1")) == 1 and "Europe/Berlin" in only(r, "T1")[0]["explanation"]


def test_T1_P0_outside_window_with_unknown_timezone_is_silent(ev):
    """§6.1: P0 may page at any hour, so an unresolvable owner timezone changes nothing for a P0 — no T1 at all."""
    d = _notify_at(happy_dossier(), "2026-03-02T23:30:00Z")
    d["scoring"]["severity"] = "P0"
    d["actions"][2]["params"]["severity"] = "P0"
    orig = ev.cx.owners["u_T"]["timezone"]
    ev.cx.owners["u_T"]["timezone"] = "Nowhere/Nope"
    try:
        assert "T1" not in rules(ev.evaluate(d))
    finally:
        ev.cx.owners["u_T"]["timezone"] = orig


# ── T2: ≥ 6h between notifications, ≤ 3 total, before acknowledgement ───────────────────────────

def test_T2_gap_of_exactly_six_hours_is_allowed_and_one_second_less_is_not(ev):
    """spec §6.2 'at least 6 hours'. Acknowledged 03-03 12:00Z; two notifications 14:00Z → 20:00Z (6.0h) are fine,
    14:00Z → 19:59:59Z is T2. 20:00Z is 21:00 Berlin, so T1 also fires — not under test."""
    ok = _notify_at(happy_dossier(), "2026-03-02T14:00:00Z", "2026-03-02T20:00:00Z")
    assert "T2" not in rules(ev.evaluate(ok))
    bad = _notify_at(happy_dossier(), "2026-03-02T14:00:00Z", "2026-03-02T19:59:59Z")
    t2 = only(ev.evaluate(bad), "T2")
    assert len(t2) == 1 and t2[0]["step"] == 6 and "min 6h" in t2[0]["explanation"] and t2[0]["severity"] == SEV_WEIGHT["high"]


def test_T2_exactly_three_notifications_is_allowed(ev):
    """spec §6.2 'no more than 3 notifications in total'. Three, each ≥ 6h apart, all before acknowledgement
    (moved to 03-04) → no T2."""
    d = _notify_at(happy_dossier(), "2026-03-02T14:00:00Z", "2026-03-03T08:00:00Z", "2026-03-03T15:00:00Z")
    d["lifecycle"][-1]["at"] = "2026-03-04T12:00:00Z"
    d["closed_at"] = "2026-03-04T12:00:00Z"
    assert "T2" not in rules(ev.evaluate(d))


def test_T2_fourth_notification_after_acknowledgement_does_not_count(ev):
    """spec §6.2 conditions spacing and count on 'If an owner has not acknowledged'. Three before the 03-03 12:00Z
    acknowledgement and one after → no T2."""
    d = _notify_at(happy_dossier(), "2026-03-02T14:00:00Z", "2026-03-02T20:30:00Z", "2026-03-03T11:00:00Z", "2026-03-03T13:00:00Z")
    assert "T2" not in rules(ev.evaluate(d))


# ── T3: opened_at → first notify_owner, per-severity target, closed bound ────────────────────────

@pytest.mark.parametrize("sev,at,expect", [
    ("P2", "2026-03-05T08:00:00Z", False),   # exactly 72h
    ("P2", "2026-03-05T08:01:00Z", True),    # 72h + 1min
    ("P0", "2026-03-02T12:00:00Z", False),   # exactly 4h
    ("P0", "2026-03-02T12:01:00Z", True),
    ("P1", "2026-03-03T08:00:00Z", False),   # exactly 24h
    ("P3", "2026-03-09T08:00:00Z", False),   # exactly 168h
    ("P3", "2026-03-09T08:00:01Z", True),
])
def test_T3_target_is_a_closed_bound(ev, sev, at, expect):
    """spec §6.3: 'inside a window set by its severity' — the target hour itself is inside. Opened 03-02 08:00Z;
    the notification trails the routed edge, which is moved to the notification instant so I4 stays clean."""
    d = _notify_at(happy_dossier(), at)
    d["scoring"]["severity"] = sev
    d["actions"][2]["params"]["severity"] = sev
    d["lifecycle"][4]["at"] = "2026-03-02T11:20:00Z"        # enrichment back and scored before 12:00Z so the
    d["lifecycle"][5]["at"] = "2026-03-02T11:40:00Z"        # P0 4h case keeps strictly increasing timestamps
    d["actions"][2]["at"] = "2026-03-02T11:40:00Z"
    d["lifecycle"][6]["at"] = at
    d["lifecycle"][-1]["at"] = "2026-03-12T12:00:00Z"
    d["closed_at"] = "2026-03-12T12:00:00Z"
    t3 = only(ev.evaluate(d), "T3")
    assert bool(t3) is expect, (sev, at)
    if expect:
        assert t3[0]["step"] == 6 and f"{sev} target" in t3[0]["explanation"] and t3[0]["severity"] == SEV_WEIGHT["high"]


def test_T3_routed_with_no_notification_at_all(ev):
    """spec §6.3 measures to the first notify_owner; a scored→routed edge with no notification ever sent cannot
    have met any target → T3 on the routed step ('routed with no notify_owner')."""
    d = happy_dossier()
    d["notifications"] = []
    d["actions"] = d["actions"][:3]
    t3 = only(ev.evaluate(d), "T3")
    assert len(t3) == 1 and t3[0]["step"] == 6 and "no notify_owner" in t3[0]["explanation"]


def test_T3_is_measured_from_opened_at_not_from_the_scored_edge(ev):
    """spec §6.3 'measured from opened_at'. Open 03-02 08:00Z, scored/routed late, first notify 03-05 09:00Z =
    73h → T3 for P2 even though the notification followed the routed edge within the hour."""
    d = _notify_at(happy_dossier(), "2026-03-05T09:00:00Z")
    d["lifecycle"][5]["at"] = "2026-03-05T08:00:00Z"
    d["lifecycle"][6]["at"] = "2026-03-05T08:30:00Z"
    d["lifecycle"][-1]["at"] = "2026-03-06T12:00:00Z"
    d["actions"][2]["at"] = "2026-03-05T08:00:00Z"
    d["closed_at"] = "2026-03-06T12:00:00Z"
    t3 = only(ev.evaluate(d), "T3")
    assert len(t3) == 1 and "73.0h" in t3[0]["explanation"]


# ── T4: 14 idle days, boundaries ────────────────────────────────────────────────────────────────

def _expired(d, at):
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": at, "trigger": "staleness_timeout", "reason": ""}
    d["closed_at"] = at
    d["decision"]["disposition"] = "expired"
    return d


def test_T4_expiry_at_exactly_fourteen_idle_days_is_allowed(ev):
    """spec §6.4 'no new evidence has been attached for 14 days (20,160 minutes)'. Last evidence 03-02 09:30Z;
    expiry 03-16 09:30Z is exactly 14 days → allowed; 03-16 09:29Z is 13 days 23h59m → premature T4."""
    assert "T4" not in rules(ev.evaluate(_expired(happy_dossier(), "2026-03-16T09:30:00Z")))
    t4 = only(ev.evaluate(_expired(happy_dossier(), "2026-03-16T09:29:00Z")), "T4")
    assert len(t4) == 1 and t4[0]["step"] == 7 and "13 idle days" in t4[0]["explanation"]


def test_T4_still_open_at_exactly_fourteen_idle_days_is_overdue(ev):
    """§6.4 the other way: still routed, no acknowledgement, last observed event exactly 14 days after the last
    evidence → 'should have expired' at half weight (the record may simply end before the expiry event)."""
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:-1]
    d["lifecycle"][-1]["at"] = "2026-03-16T09:30:00Z"
    d["actions"][3]["at"] = "2026-03-16T09:30:00Z"
    d["notifications"][0]["at"] = "2026-03-16T09:30:00Z"
    d["closed_at"] = None
    d["decision"]["disposition"] = "routed"
    t4 = only(ev.evaluate(d), "T4")
    assert len(t4) == 1 and t4[0]["step"] == 6 and "14 days" in t4[0]["explanation"]
    assert t4[0]["severity"] == pytest.approx(SEV_WEIGHT["medium"] * UNCERTAIN_FACTOR)
    d["lifecycle"][-1]["at"] = "2026-03-16T09:29:00Z"
    d["actions"][3]["at"] = "2026-03-16T09:29:00Z"
    d["notifications"][0]["at"] = "2026-03-16T09:29:00Z"
    assert "T4" not in rules(ev.evaluate(d))


def test_T4_idle_clock_starts_at_opened_at_when_no_evidence_was_ever_attached(ev):
    """§6.4 'no new evidence attached for 14 days': with no evidence at all the clock runs from opened_at
    (03-02 08:00Z). Expiry 03-10 → 8 idle days → T4; expiry 03-17 → 15 → allowed."""
    d = _expired(happy_dossier(), "2026-03-10T12:00:00Z")
    d["evidence"] = []
    d["actions"] = [a for a in d["actions"] if a["action"] != "attach_evidence"]
    t4 = only(ev.evaluate(d), "T4")
    assert len(t4) == 1 and "8 idle days" in t4[0]["explanation"]
    d = _expired(happy_dossier(), "2026-03-17T12:00:00Z")
    d["evidence"] = []
    d["actions"] = [a for a in d["actions"] if a["action"] != "attach_evidence"]
    assert "T4" not in rules(ev.evaluate(d))


# ── P6: channel, locale, and the right person ───────────────────────────────────────────────────

def test_P6_notifying_a_different_owner_than_the_accounts(ev):
    """spec §6.1 'the owner named on the signal' / §8.6: a notification to u_OTHER while metadata.owner_id is u_T
    → P6 naming both ids, soft 0.1."""
    d = happy_dossier()
    d["notifications"][0]["owner_id"] = "u_OTHER"
    p6 = only(ev.evaluate(d), "P6")
    assert len(p6) == 1 and "u_OTHER" in p6[0]["explanation"] and "u_T" in p6[0]["explanation"] and p6[0]["severity"] == SEV_WEIGHT["soft"]


def test_P6_wrong_channel_and_locale_are_two_findings_on_one_notification(ev):
    """§8.6 names two properties; both wrong on one notification → two P6 entries at the same step."""
    d = happy_dossier()
    d["notifications"][0].update(channel="email", locale="en-US")
    p6 = only(ev.evaluate(d), "P6")
    assert len(p6) == 2 and {v["step"] for v in p6} == {6}
    assert any("channel" in v["explanation"] for v in p6) and any("locale" in v["explanation"] for v in p6)


def test_P6_owner_record_wins_over_metadata_snapshot(ev):
    """The owners table (slack/de-DE) is authoritative when loaded: metadata claiming email/en-US does not excuse
    an email/en-US notification."""
    d = happy_dossier()
    d["metadata"].update(owner_preferred_channel="email", owner_locale="en-US")
    d["notifications"][0].update(channel="email", locale="en-US")
    assert len(only(ev.evaluate(d), "P6")) == 2
