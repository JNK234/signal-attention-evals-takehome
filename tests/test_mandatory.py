"""
ABOUTME: Known-answer tests for the mandatory-route check (spec §8.1 / §4.3): triggers_from_reading() over
ABOUTME: synthetic artefacts with meaning injected through the TableLabeller, so attribution and P1 are pinned without a model.
"""

import pytest

from conftest import ARTIFACT, explain, happy_dossier, rules, with_labels
from signal_eval.checks.mandatory import triggers_from_reading
from signal_eval.util import ts

INTERNAL_NOTE = dict(ARTIFACT, artifact_id="art_INT", type="internal_note", source="crm_note", author="Owner",
                     author_type="internal", text="Their counsel has asked for the master agreement; legal is looping in.")
SECOND_CUSTOMER = dict(ARTIFACT, artifact_id="art_T2", type="email", source="email", author="Grace",
                       timestamp="2026-03-01T12:00:00Z", text="Following up on the backfill plan.")
THREAD = ("Sorted, thanks. Ignore the thread below.\n\n"
          "On 20 Sep 2025, Kenji Iyer wrote:\n"
          "> Third time this week, we are done with this vendor.")


def suppressed(d, reason="immaterial"):
    """Close the happy dossier at step 6 by suppression with nobody notified."""
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed",
                                           "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": reason}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": reason}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only", reason=reason)
    return d


def attach(d, *arts):
    """Attach each artefact quoting its first line (always current, depth-0 text)."""
    d["evidence"] = [{"step": 2, "artifact_id": a["artifact_id"], "source": a["source"], "restricted": False,
                      "attached_at": "2026-03-02T09:30:00Z", "quote": a["text"].split("\n")[0][:40]} for a in arts]
    return d


def billing_event(amount, artifact_id="art_BILL", status="disputed by customer AP", currency="$"):
    amt = f"{currency}{amount:,}" if currency in "$€£" else f"{amount:,} {currency}"
    return dict(ARTIFACT, artifact_id=artifact_id, type="billing_event", source="billing_event", author="billing-bot",
                author_type="bot", text=f"Invoice INV-9 for {amt} issued. Status: {status}.")


def p1s(result):
    return [v for v in result["violations"] if v["rule"] == "§8.1"]


def case(ev, arts, table, dossier=None, detector=None, dtr=None):
    """Suppressed happy dossier with `arts` attached (quote = head of each text) read under `table`."""
    d = dossier or suppressed(attach(happy_dossier(), *arts))
    if detector:
        d["detector"] = detector
    if dtr is not None:
        d["metadata"]["days_to_renewal"] = dtr
    for a in arts:
        ev.cx.artifacts[a["artifact_id"]] = a
    with_labels(ev, table)
    return explain(ev, d)


def src(r):
    return r["_facts"]["trigger_source"]


# ── P1 fires on suppression or no human, whatever came first (spec §4.3, §8.1) ────────────────────

def test_notified_then_suppressed_with_trigger_is_P1(ev):
    d = attach(happy_dossier(), billing_event(20_000))
    d["detector"] = "billing_dispute"
    ev.cx.artifacts["art_BILL"] = billing_event(20_000)
    d["lifecycle"] = d["lifecycle"][:7] + [{"step": 7, "from_state": "routed", "to_state": "suppressed",
                                           "at": "2026-03-03T12:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"].append({"step": 7, "action": "suppress", "at": "2026-03-03T12:00:00Z", "params": {"reason": "immaterial"}})
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    r = explain(ev, d)
    assert r["_facts"]["reached_human"] is True
    hits = p1s(r)
    assert len(hits) == 1 and hits[0]["step"] == 7
    assert "suppressed" in hits[0]["explanation"] and "billing_dispute" in hits[0]["explanation"]
    assert hits[0]["severity"] == 1.0


def test_trigger_routed_and_acknowledged_is_not_P1(ev):
    d = attach(happy_dossier(), billing_event(20_000))
    d["detector"] = "billing_dispute"
    ev.cx.artifacts["art_BILL"] = billing_event(20_000)
    r = explain(ev, d)
    assert "billing_dispute" in r["_facts"]["triggers"] and "§8.1" not in rules(r)


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


# ── cancel intent: depth 0, customer author (spec §8.1 bullet 1) ──────────────────────────────────

def test_depth0_customer_cancel_is_confirmed_trigger(ev):
    art = dict(SECOND_CUSTOMER, text="We love the product. That said, we will not be renewing.")
    r = case(ev, [art], {"not be renewing": {"cancel_intent": 0.9}})
    assert src(r)["confirmed"] == ["cancel_intent"] and src(r)["uncertain"] == []
    hits = p1s(r)
    assert hits and hits[0]["severity"] == 1.0 and hits[0]["step"] == 6
    assert "confirmed ['cancel_intent']" in hits[0]["explanation"]


def test_cancel_only_in_quoted_history_is_historical_not_trigger(ev):
    """docs/domain.md 'Quoted history': the current message is 'Sorted, thanks'; the cancel line sits at depth 1."""
    art = dict(SECOND_CUSTOMER, text=THREAD)
    r = case(ev, [art], {"done with this vendor": {"cancel_intent": 0.9}})
    assert src(r)["historical"] == ["cancel_intent"] and r["_facts"]["triggers"] == []
    assert "§8.1" not in rules(r)


def test_internal_report_of_cancel_is_fact_not_trigger(ev):
    """A9: an internal author writing that the customer is cancelling is not 'from a customer-side author'."""
    art = dict(INTERNAL_NOTE, text="Call with Ada: they told us they will not renew in June.")
    r = case(ev, [art], {"will not renew": {"cancel_intent": 0.9}})
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    assert src(r)["per_artifact"]["art_INT"]["facts"]["cancel_intent"] == "reported_by_internal"


def test_bot_artifact_contributes_nothing_but_billing(ev):
    art = dict(SECOND_CUSTOMER, artifact_id="art_BOT", type="bot_alert", source="bot_alert", author="alerts", author_type="bot",
               text="Auto-summary: customer says they will not renew; counsel engaged.")
    r = case(ev, [art], {"not renew": {"cancel_intent": 0.9, "legal_reference": 0.9, "security_incident": 0.9}})
    assert r["_facts"]["triggers"] == [] and src(r)["historical"] == [] and "§8.1" not in rules(r)


def test_forwarded_text_about_another_account_is_masked(ev):
    """spec §8.4: a forward naming another customer is a property of the source data; its triggers are not this account's."""
    art = dict(SECOND_CUSTOMER, mentions_other_account="Other Co",
               text="FYI, see below.\n\n---------- Forwarded message ----------\nFrom: Other Co\nSent: Monday\nOther Co is cancelling their contract.")
    r = case(ev, [art], {"cancelling": {"cancel_intent": 0.9}})
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    assert src(r)["per_artifact"]["art_T2"]["facts"]["masked_other_account"] == "Other Co"


def test_depth0_block_naming_another_account_is_masked(ev):
    art = dict(SECOND_CUSTOMER, mentions_other_account="Other Co",
               text="Heard from Other Co that they are cancelling; we are staying put.")
    r = case(ev, [art], {"cancelling": {"cancel_intent": 0.9}})
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    assert src(r)["per_artifact"]["art_T2"]["facts"]["masked_other_account"] == "Other Co"


def test_signature_block_is_not_read(ev):
    """The champion's name in the sender's signature does not make the departure about the champion."""
    art = dict(SECOND_CUSTOMER, text="Our intern resigned last week; no impact on the rollout.\n\nBest,\nAda Lovelace\nada@testco.com | +1-415-555-0100")
    r = case(ev, [art], {"intern resigned": {"departure": 0.9}}, dtr=60)
    assert r["_facts"]["triggers"] == [] and src(r)["unattributed"] == ["departure"]


# ── legal reference: assertion, any non-bot author (A4) ───────────────────────────────────────────

LEGAL = {"reserve all rights": {"legal_reference": 0.9}, "ask them for the badge": {"legal_reference": 0.1}}


def test_legal_assertion_is_trigger_but_mention_is_not(ev):
    assertion = dict(SECOND_CUSTOMER, text="We reserve all rights under the MSA pending your response.")
    mention = dict(SECOND_CUSTOMER, artifact_id="art_T3", text="Can you ask them for the badge for the legal team's visit?")
    r = case(ev, [assertion], LEGAL)
    assert src(r)["confirmed"] == ["legal_reference"] and p1s(r)[0]["severity"] == 1.0
    r = case(ev, [mention], LEGAL)
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)


def test_internal_note_with_legal_reference_is_trigger(ev):
    r = case(ev, [INTERNAL_NOTE], {"counsel": {"legal_reference": 0.9}})
    assert src(r)["confirmed"] == ["legal_reference"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "legal_reference" in hits[0]["explanation"]


# ── security incident: raised by the customer (spec §8.1 bullet 3) ────────────────────────────────

def test_security_incident_needs_customer_author(ev):
    table = {"exfiltrated": {"security_incident": 0.9}}
    internal = dict(INTERNAL_NOTE, text="Their SOC says data was exfiltrated from a shared workspace.")
    r = case(ev, [internal], table)
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    customer = dict(SECOND_CUSTOMER, text="Our SOC confirmed data was exfiltrated from the shared workspace.")
    r = case(ev, [customer], table)
    assert src(r)["confirmed"] == ["security_incident"] and p1s(r)


# ── departure: resolved subject inside the 90-day window (spec §8.1 bullet 4, A5) ─────────────────

DEPART = {"moving on": {"departure": 0.9}, "is leaving": {"departure": 0.9}, "resigned": {"departure": 0.9}}


def test_departure_authored_by_champion_is_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    r = case(ev, [art], DEPART, dtr=60)
    assert src(r)["confirmed"] == ["buyer_or_champion_departure"]
    assert "author" in src(r)["per_artifact"]["art_T2"]["facts"]["buyer_or_champion_departure"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "buyer_or_champion_departure" in hits[0]["explanation"]


def test_departure_naming_economic_buyer_in_text_is_trigger(ev):
    art = dict(SECOND_CUSTOMER, text="Heads up: Alan Turing is leaving the company next month.")
    r = case(ev, [art], DEPART, dtr=60)
    assert src(r)["confirmed"] == ["buyer_or_champion_departure"]


def test_departure_by_role_phrase_from_account_titles_is_trigger(ev):
    art = dict(SECOND_CUSTOMER, text="Our head of analytics is leaving at the end of the month.")
    ev.cx.accounts["acct_T"]["champion_title"] = "Head of Analytics"
    try:
        r = case(ev, [art], DEPART, dtr=60)
    finally:
        ev.cx.accounts["acct_T"].pop("champion_title")
    assert src(r)["confirmed"] == ["buyer_or_champion_departure"]
    assert "role" in src(r)["per_artifact"]["art_T2"]["facts"]["buyer_or_champion_departure"]


def test_one_word_title_is_not_a_role_phrase(ev):
    art = dict(SECOND_CUSTOMER, text="Our director is leaving at the end of the month.")
    ev.cx.accounts["acct_T"]["champion_title"] = "Director"
    try:
        r = case(ev, [art], DEPART, dtr=60)
    finally:
        ev.cx.accounts["acct_T"].pop("champion_title")
    assert r["_facts"]["triggers"] == [] and src(r)["unattributed"] == ["departure"]


def test_departure_of_unnamed_person_is_unattributed(ev):
    art = dict(SECOND_CUSTOMER, text="Our intern resigned last week; no impact on the rollout.")
    r = case(ev, [art], DEPART, dtr=60)
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    assert src(r)["unattributed"] == ["departure"]
    assert src(r)["per_artifact"]["art_T2"]["facts"]["departure"] == "unattributed"


@pytest.mark.parametrize("text", ["We are planning a backfill of about 90M rows.",
                                  "[jira] PLAT-812 transitioned to Done by Ada Lovelace",
                                  "Leaving the history below for context."])
def test_departure_decoys_scoring_zero_leave_nothing(ev, text):
    art = dict(SECOND_CUSTOMER, text=text)
    r = case(ev, [art], {}, dtr=60)
    assert r["_facts"]["triggers"] == [] and src(r)["unattributed"] == [] and "§8.1" not in rules(r)


def test_named_departure_outside_renewal_window_is_fact_not_trigger(ev):
    art = dict(SECOND_CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    r = case(ev, [art], DEPART, dtr=200)
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
    assert "outside" in src(r)["per_artifact"]["art_T2"]["facts"]["buyer_or_champion_departure"]


# ── billing dispute: structural read of the billing_event block (spec §8.1 bullet 5, A1/A7) ───────

def test_billing_po_mismatch_above_five_percent_is_confirmed(ev):
    bill = billing_event(88_000, status="disputed (PO_MISMATCH)")
    r = case(ev, [bill], {}, detector="billing_dispute")       # ARR 100,000 → 88%
    assert src(r)["confirmed"] == ["billing_dispute"] and p1s(r)[0]["severity"] == 1.0
    r = case(ev, [bill], {"PO_MISMATCH": {"billing_dispute": 0.9}}, detector="seat_decay")   # verdict, no detector
    assert src(r)["confirmed"] == ["billing_dispute"]


def test_credit_memo_labelled_not_disputed_is_nothing(ev):
    memo = billing_event(88_000, status="credit memo issued, dispute resolved")
    r = case(ev, [memo], {"credit memo": {"billing_dispute": 0.1}}, detector="seat_decay")
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)


def test_non_usd_amount_is_uncertain(ev):
    bill = billing_event(88_000, currency="€")
    r = case(ev, [bill], {}, detector="billing_dispute")
    assert src(r)["confirmed"] == [] and src(r)["uncertain"] == ["billing_dispute(amount unverified)"]
    hits = p1s(r)
    assert hits and hits[0]["severity"] == 0.5 and "uncertain" in hits[0]["explanation"]


def test_billing_dispute_at_exactly_five_percent_is_not_trigger(ev):
    r = case(ev, [billing_event(5_000)], {}, detector="billing_dispute")      # ARR 100,000
    assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)


def test_billing_dispute_just_above_five_percent_is_trigger(ev):
    r = case(ev, [billing_event(5_001)], {}, detector="billing_dispute")
    assert src(r)["confirmed"] == ["billing_dispute"] and p1s(r)[0]["step"] == 6


# ── abstain is uncertain; unread is UNEVALUATED — neither is silent ──────────────────────────────

def test_abstain_on_customer_block_is_uncertain_P1(ev):
    art = dict(SECOND_CUSTOMER, text="Honestly not sure we can keep this going next year.")
    r = case(ev, [art], {"keep this going": {"cancel_intent": 0.5}})       # inside the (0.35, 0.65) band
    assert src(r)["confirmed"] == [] and src(r)["uncertain"] == ["cancel_intent"]
    hits = p1s(r)
    assert hits and hits[0]["severity"] == 0.5 and "uncertain ['cancel_intent']" in hits[0]["explanation"]
    assert r["_facts"]["triggers"] == ["cancel_intent"]


def test_unread_customer_block_is_unevaluated_not_a_trigger(ev):
    """A block the labeller could not read carries no score for any label. That is not the model abstaining on
    something it saw — nobody looked — so it must not become four half-severity triggers. The evaluator's single
    UNEVALUATED entry is the honest record; the per-artefact facts name the unread labels."""
    from signal_eval.labellers import TableLabeller
    art = dict(SECOND_CUSTOMER, text="garbled bytes here")
    d = suppressed(attach(happy_dossier(), art))
    ev.cx.artifacts["art_T2"] = art
    ev.cx.set_labeller(TableLabeller({}, unreadable={"garbled"}))
    r = explain(ev, d)
    assert src(r)["confirmed"] == [] and src(r)["uncertain"] == []
    assert not p1s(r)
    per = src(r)["per_artifact"]["art_T2"]
    assert {"cancel_intent", "legal_reference", "security_incident", "departure"} <= set(per["facts"]["unread"])


# ── unattached-trigger window ends at close (or opened + 14d) ─────────────────────────────────────

def _unattached(ev, when, closed_at="2026-03-03T12:00:00Z"):
    ev.cx.account_triggers["acct_T"] = [(ts(when), "art_LATE", {"legal_reference"})]
    d = suppressed(happy_dossier())
    d["closed_at"] = closed_at
    return explain(ev, d)


def test_trigger_written_after_close_is_not_unattached(ev):
    r = _unattached(ev, "2026-03-05T10:00:00Z")
    assert r["_facts"]["account_triggers_unattached"] == [] and "§8.1" not in rules(r)


def test_trigger_written_while_open_is_unattached_and_uncertain(ev):
    r = _unattached(ev, "2026-03-02T20:00:00Z")
    assert [aid for _, aid, _ in r["_facts"]["account_triggers_unattached"]] == ["art_LATE"]
    hits = p1s(r)
    assert hits and hits[0]["step"] == 6 and "unattached" in hits[0]["explanation"] and hits[0]["severity"] == 0.5


def test_trigger_before_lookback_is_not_unattached(ev):
    r = _unattached(ev, "2026-01-15T10:00:00Z")
    assert r["_facts"]["account_triggers_unattached"] == []


def test_unknown_close_uses_fourteen_day_window(ev):
    assert _unattached(ev, "2026-03-10T10:00:00Z", closed_at=None)["_facts"]["account_triggers_unattached"]
    assert not _unattached(ev, "2026-03-20T10:00:00Z", closed_at=None)["_facts"]["account_triggers_unattached"]


# ── triggers_from_reading directly: the same rules feed the account-level index ──────────────────

def _read(ev, table, art):
    with_labels(ev, table)
    return ev.cx.read_artifact(art)


def test_reading_legal_reference_from_internal_author_is_confirmed(ev):
    t = triggers_from_reading(_read(ev, {"counsel": {"legal_reference": 0.9}}, INTERNAL_NOTE), INTERNAL_NOTE, ev.cx.accounts["acct_T"], 60)
    assert t["confirmed"] == {"legal_reference"} and not t["uncertain"]


def test_reading_departure_requires_named_person(ev):
    acc = ev.cx.accounts["acct_T"]
    d1 = dict(SECOND_CUSTOMER, artifact_id="art_D1", text="Our intern resigned.")
    d2 = dict(SECOND_CUSTOMER, artifact_id="art_D2", author="Alan Turing", text="I am leaving.")
    d3 = dict(SECOND_CUSTOMER, artifact_id="art_D3", text="Ada Lovelace has resigned.")
    table = {"resigned": {"departure": 0.9}, "leaving": {"departure": 0.9}}
    assert triggers_from_reading(_read(ev, table, d1), d1, acc, 60)["unattributed"] == {"departure"}
    assert triggers_from_reading(_read(ev, table, d1), d1, acc, 60)["confirmed"] == set()
    assert triggers_from_reading(_read(ev, table, d2), d2, acc, 60)["confirmed"] == {"buyer_or_champion_departure"}
    assert triggers_from_reading(_read(ev, table, d3), d3, acc, 60)["confirmed"] == {"buyer_or_champion_departure"}
    # the window is the signal's: the index reads without it, the per-dossier scan applies it
    assert triggers_from_reading(_read(ev, table, d3), d3, acc, 200)["confirmed"] == set()
    assert triggers_from_reading(_read(ev, table, d3), d3, acc, None, check_window=False)["confirmed"] == {"buyer_or_champion_departure"}


def test_reading_security_still_requires_customer(ev):
    t = triggers_from_reading(_read(ev, {"counsel": {"security_incident": 0.9}}, INTERNAL_NOTE), INTERNAL_NOTE, ev.cx.accounts["acct_T"], 60)
    assert t["confirmed"] == set() and t["uncertain"] == set()


def test_index_uses_confirmed_triggers_only(ev):
    """context._index_triggers feeds account_triggers from the confirmed set; abstains are not indexed."""
    sure = dict(SECOND_CUSTOMER, artifact_id="art_S", text="We will not be renewing.")
    maybe = dict(SECOND_CUSTOMER, artifact_id="art_M", text="Not sure we can keep this going.")
    with_labels(ev, {"not be renewing": {"cancel_intent": 0.9}, "keep this going": {"cancel_intent": 0.5}})
    ev.cx.label_artifact(sure)
    ev.cx.label_artifact(maybe)
    indexed = {aid: t for _, aid, t in ev.cx.account_triggers["acct_T"]}
    assert indexed == {"art_S": {"cancel_intent"}}
