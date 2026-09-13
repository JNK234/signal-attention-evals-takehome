"""
ABOUTME: Known-answer tests for the mandatory-route check (spec §8.1 / §4.3) with synthetic NLI labels
ABOUTME: injected in place of the model, so trigger attribution and the P1 rule are pinned without any inference.
"""

from conftest import ARTIFACT, happy_dossier, rules
from signal_eval.classifier import TRIGGER_LABELS
from signal_eval.util import ts

INTERNAL_NOTE = dict(ARTIFACT, artifact_id="art_INT", type="internal_note", source="crm_note", author="Owner",
                     author_type="internal", text="Their counsel has asked for the master agreement; legal is looping in.")
SECOND_CUSTOMER = dict(ARTIFACT, artifact_id="art_T2", type="email", source="email", author="Grace",
                       timestamp="2026-03-01T12:00:00Z", text="Following up on the backfill plan.")


def label(**kw):
    """A neutral label dict in the shape classifier.assemble() builds; override any key."""
    out = {k: False for k in TRIGGER_LABELS}
    out.update(sarcasm=False, topic=None, triggers_belong_elsewhere=False, unverifiable=False, stale=False,
               quoted_history=False, scores={k: 0.0 for k in TRIGGER_LABELS})
    out.update(kw)
    return out


def use_labels(ev, by_id):
    """Route check_evidence's label lookup to a fixed table instead of the NLI model."""
    ev.cx.use_classifier = True
    ev.cx.classifier_active = True
    ev.cx.label_artifact = lambda art: by_id.get(art.get("artifact_id"), label())


def suppressed(d, reason="immaterial"):
    """Close the happy dossier at step 6 by suppression with nobody notified."""
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed",
                                           "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": reason}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": reason}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only", reason=reason)
    return d


def attach(d, *arts):
    d["evidence"] = [{"step": 2, "artifact_id": a["artifact_id"], "source": a["source"], "restricted": False,
                      "attached_at": "2026-03-02T09:30:00Z", "quote": a["text"][:40]} for a in arts]
    return d


def billing_event(amount, artifact_id="art_BILL"):
    return dict(ARTIFACT, artifact_id=artifact_id, type="billing_event", source="billing_event", author="billing-bot",
                author_type="bot", text=f"Invoice INV-9 for ${amount:,} issued. Status: disputed by customer AP.")


def p1s(result):
    return [v for v in result["violations"] if v["rule"] == "P1"]


# ── finding 1: suppression is a violation even after a human was notified (spec §4.3, §8.1) ──────────

def test_notified_then_suppressed_with_trigger_is_P1(ev):
    d = attach(happy_dossier(), billing_event(20_000))
    d["detector"] = "billing_dispute"
    ev.cx.artifacts["art_BILL"] = billing_event(20_000)
    d["lifecycle"] = d["lifecycle"][:7] + [{"step": 7, "from_state": "routed", "to_state": "suppressed",
                                           "at": "2026-03-03T12:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"].append({"step": 7, "action": "suppress", "at": "2026-03-03T12:00:00Z", "params": {"reason": "immaterial"}})
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    r = ev.evaluate(d)
    assert r["_facts"]["reached_human"] is True
    hits = p1s(r)
    assert len(hits) == 1 and hits[0]["step"] == 7
    assert "suppressed" in hits[0]["explanation"] and "billing_dispute" in hits[0]["explanation"]
    assert hits[0]["severity"] == 1.0


def test_trigger_routed_and_acknowledged_is_not_P1(ev):
    d = attach(happy_dossier(), billing_event(20_000))
    d["detector"] = "billing_dispute"
    ev.cx.artifacts["art_BILL"] = billing_event(20_000)
    r = ev.evaluate(d)
    assert "billing_dispute" in r["_facts"]["triggers"] and "P1" not in rules(r)


def test_trigger_with_no_human_and_no_suppression_is_still_P1(ev):
    d = attach(happy_dossier(), billing_event(20_000))
    d["detector"] = "billing_dispute"
    ev.cx.artifacts["art_BILL"] = billing_event(20_000)
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "expired",
                                           "at": "2026-03-20T12:00:00Z", "trigger": "staleness_timeout", "reason": ""}]
    d["actions"] = d["actions"][:3]
    d["notifications"] = []
    d["decision"].update(disposition="expired", recommended_play="watch_only")
    hits = p1s(ev.evaluate(d))
    assert len(hits) == 1 and hits[0]["step"] == 6 and "without any human notified" in hits[0]["explanation"]


# ── finding 2: departure counts only for the economic buyer or named champion (spec §8.1 bullet 4) ──

def _departure_case(ev, art):
    d = suppressed(attach(happy_dossier(), art))
    d["metadata"]["days_to_renewal"] = 60
    ev.cx.artifacts[art["artifact_id"]] = art
    use_labels(ev, {art["artifact_id"]: label(departure=True, scores={**label()["scores"], "departure": 0.9})})
    return ev.evaluate(d)


def test_departure_authored_by_champion_is_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    r = _departure_case(ev, art)
    assert "buyer_or_champion_departure" in r["_facts"]["triggers"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "buyer_or_champion_departure" in hits[0]["explanation"]


def test_departure_naming_economic_buyer_in_text_is_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Grace", text="Heads up: Alan Turing is leaving the company next month.")
    r = _departure_case(ev, art)
    assert "buyer_or_champion_departure" in r["_facts"]["triggers"]


def test_departure_of_unnamed_person_is_not_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Grace", text="Our intern resigned last week; no impact on the rollout.")
    r = _departure_case(ev, art)
    assert "buyer_or_champion_departure" not in r["_facts"]["triggers"]
    assert "P1" not in rules(r)


def test_named_departure_outside_renewal_window_is_not_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    d = suppressed(attach(happy_dossier(), art))
    d["metadata"]["days_to_renewal"] = 200
    ev.cx.artifacts[art["artifact_id"]] = art
    use_labels(ev, {art["artifact_id"]: label(departure=True)})
    assert "buyer_or_champion_departure" not in ev.evaluate(d)["_facts"]["triggers"]


# ── legal reference: any author (spec §8.1 bullet 2 has no author restriction) ──────────────────────

def test_internal_note_with_legal_reference_is_trigger(ev):
    d = suppressed(attach(happy_dossier(), INTERNAL_NOTE))
    ev.cx.artifacts["art_INT"] = INTERNAL_NOTE
    use_labels(ev, {"art_INT": label(legal_reference=True)})
    r = ev.evaluate(d)
    assert "legal_reference" in r["_facts"]["triggers"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "legal_reference" in hits[0]["explanation"]


def test_internal_note_with_security_label_is_not_trigger(ev):
    """Bullet 3 is 'raised by the customer' — the author restriction stays there."""
    d = suppressed(attach(happy_dossier(), INTERNAL_NOTE))
    ev.cx.artifacts["art_INT"] = INTERNAL_NOTE
    use_labels(ev, {"art_INT": label(security_incident=True)})
    assert "security_incident" not in ev.evaluate(d)["_facts"]["triggers"]


# ── finding 3: partial label coverage cannot erase the structural cancel proxy ───────────────────────

def _churn_case(ev, labels):
    d = suppressed(attach(happy_dossier(), ARTIFACT, SECOND_CUSTOMER))
    d["detector"] = "exec_churn_language"
    ev.cx.artifacts["art_T2"] = SECOND_CUSTOMER
    use_labels(ev, labels)
    return ev.evaluate(d)


def test_partial_coverage_keeps_cancel_proxy(ev):
    r = _churn_case(ev, {"art_T1": label(), "art_T2": label(unverifiable=True)})
    assert "cancel_intent" in r["_facts"]["triggers"]
    assert r["_facts"]["trigger_source"]["removed_by_model"] == []
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "proxy" in hits[0]["explanation"] and hits[0]["severity"] < 1.0


def test_full_coverage_removes_cancel_proxy(ev):
    r = _churn_case(ev, {"art_T1": label(), "art_T2": label()})
    assert "cancel_intent" not in r["_facts"]["triggers"]
    assert r["_facts"]["trigger_source"]["removed_by_model"] == ["cancel_intent"]
    assert "P1" not in rules(r)


def test_model_addition_allowed_with_partial_coverage(ev):
    r = _churn_case(ev, {"art_T1": label(legal_reference=True), "art_T2": label(unverifiable=True)})
    assert {"cancel_intent", "legal_reference"} <= set(r["_facts"]["triggers"])
    assert r["_facts"]["trigger_source"]["added_by_model"] == ["legal_reference"]
    assert p1s(r)[0]["severity"] == 1.0


# ── finding 4: unattached-trigger window ends at close (or opened + 14d) ──────────────────────────────

def _unattached(ev, when, closed_at="2026-03-03T12:00:00Z"):
    ev.cx.account_triggers["acct_T"] = [(ts(when), "art_LATE", {"legal_reference"})]
    d = suppressed(happy_dossier())
    d["closed_at"] = closed_at
    return ev.evaluate(d)


def test_trigger_written_after_close_is_not_unattached(ev):
    r = _unattached(ev, "2026-03-05T10:00:00Z")
    assert r["_facts"]["account_triggers_unattached"] == [] and "P1" not in rules(r)


def test_trigger_written_while_open_is_unattached(ev):
    r = _unattached(ev, "2026-03-02T20:00:00Z")
    assert [aid for _, aid, _ in r["_facts"]["account_triggers_unattached"]] == ["art_LATE"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "unattached" in hits[0]["explanation"]


def test_trigger_before_lookback_is_not_unattached(ev):
    r = _unattached(ev, "2026-01-15T10:00:00Z")
    assert r["_facts"]["account_triggers_unattached"] == []


def test_unknown_close_uses_fourteen_day_window(ev):
    assert _unattached(ev, "2026-03-10T10:00:00Z", closed_at=None)["_facts"]["account_triggers_unattached"]
    assert not _unattached(ev, "2026-03-20T10:00:00Z", closed_at=None)["_facts"]["account_triggers_unattached"]


# ── finding 5: billing dispute must exceed 5% of ARR (spec §8.1 bullet 5: "exceeding") ─────────────

def _billing_case(ev, amount, detector="billing_dispute", labels=None):
    bill = billing_event(amount)
    d = suppressed(attach(happy_dossier(), bill))
    d["detector"] = detector
    ev.cx.artifacts["art_BILL"] = bill
    if labels is not None:
        use_labels(ev, labels)
    return ev.evaluate(d)


def test_billing_dispute_at_exactly_five_percent_is_not_trigger(ev):
    r = _billing_case(ev, 5_000)      # ARR 100,000
    assert "billing_dispute" not in r["_facts"]["triggers"] and "P1" not in rules(r)


def test_billing_dispute_just_above_five_percent_is_trigger(ev):
    r = _billing_case(ev, 5_001)
    assert "billing_dispute" in r["_facts"]["triggers"]
    assert p1s(r)[0]["step"] == 6


def test_bot_billing_event_labelled_not_disputed_is_not_trigger(ev):
    r = _billing_case(ev, 20_000, detector="seat_decay", labels={"art_BILL": label(billing_dispute=False)})
    assert "billing_dispute" not in r["_facts"]["triggers"] and "P1" not in rules(r)


def test_bot_billing_event_labelled_disputed_is_trigger_without_detector(ev):
    r = _billing_case(ev, 20_000, detector="seat_decay", labels={"art_BILL": label(billing_dispute=True)})
    assert "billing_dispute" in r["_facts"]["triggers"]


# ── finding 6: context._index_triggers mirrors the attribution rules ────────────────────────────────

def test_index_triggers_legal_reference_from_internal_author(ev):
    ev.cx._index_triggers(INTERNAL_NOTE, label(legal_reference=True))
    assert [t for _, aid, t in ev.cx.account_triggers["acct_T"] if aid == "art_INT"] == [{"legal_reference"}]


def test_index_triggers_departure_requires_named_person(ev):
    ev.cx._index_triggers(dict(SECOND_CUSTOMER, artifact_id="art_D1", text="Our intern resigned."), label(departure=True))
    ev.cx._index_triggers(dict(SECOND_CUSTOMER, artifact_id="art_D2", author="Alan Turing", text="I am leaving."), label(departure=True))
    ev.cx._index_triggers(dict(SECOND_CUSTOMER, artifact_id="art_D3", text="Ada Lovelace has resigned."), label(departure=True))
    indexed = {aid: t for _, aid, t in ev.cx.account_triggers["acct_T"]}
    assert "art_D1" not in indexed
    assert indexed["art_D2"] == {"buyer_or_champion_departure"}
    assert indexed["art_D3"] == {"buyer_or_champion_departure"}


def test_index_triggers_security_still_requires_customer(ev):
    ev.cx._index_triggers(INTERNAL_NOTE, label(security_incident=True))
    assert not any(aid == "art_INT" for _, aid, _ in ev.cx.account_triggers["acct_T"])
