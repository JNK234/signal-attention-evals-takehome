"""
ABOUTME: Adversarial sweep of policy and containment (spec §8.1–§8.7): trigger attribution at the 90-day and
ABOUTME: amount boundaries, restricted flags with internal plays, same-type sources, contact details, cold-path P1.
"""

import pytest

from conftest import ARTIFACT, SignalEvaluator, explain, happy_dossier, rules, with_labels
from signal_eval.spec import SEV_WEIGHT, UNCERTAIN_FACTOR

CUSTOMER = dict(ARTIFACT, artifact_id="art_T2", type="email_thread", source="email_thread", author="Grace",
                timestamp="2026-03-01T12:00:00Z", text="Following up on the backfill plan.")


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def suppressed(d, reason="immaterial"):
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed",
                                           "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": reason}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": reason}}]
    d["notifications"] = []
    d["closed_at"] = "2026-03-02T14:00:00Z"
    d["decision"].update(disposition="suppressed", recommended_play="watch_only", reason=reason)
    return d


def attach(d, *arts):
    d["evidence"] = [{"step": 2, "artifact_id": a["artifact_id"], "source": a["source"], "restricted": bool(a.get("restricted")),
                      "attached_at": "2026-03-02T09:30:00Z", "quote": a["text"].split("\n")[0][:40]} for a in arts]
    return d


def case(ev, arts, table, dossier=None, detector=None, dtr=None):
    d = dossier or suppressed(attach(happy_dossier(), *arts))
    if detector:
        d["detector"] = detector
        d["lifecycle"][0]["trigger"] = f"detector:{detector}"
    if dtr is not None:
        d["metadata"]["days_to_renewal"] = dtr
    for a in arts:
        ev.cx.artifacts[a["artifact_id"]] = a
    with_labels(ev, table)
    return explain(ev, d)


def src(r):
    return r["_facts"]["trigger_source"]


# ── P2 (§8.2) ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["legal_hold", "mna_quiet_period"])
def test_P2_restricted_flag_with_internal_play_is_clean(ev, flag):
    """§8.2 'Internal routing is fine and often necessary'. watch_only / renewal_risk_review with customer_visible
    False on a flagged account → no P2 (docs/domain.md lists exactly these two plays as not customer-visible)."""
    ev.cx.accounts["acct_T"]["flags"] = [flag]
    try:
        for play in ("watch_only", "renewal_risk_review"):
            d = happy_dossier()
            d["decision"].update(recommended_play=play, customer_visible=False)
            assert "§8.2" not in rules(ev.evaluate(d)), (flag, play)
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_P2_mna_quiet_period_with_customer_visible_play(ev):
    """§8.2 names both flags; the existing test covers legal_hold only. csm_checkin on mna_quiet_period → P2 1.0."""
    ev.cx.accounts["acct_T"]["flags"] = ["mna_quiet_period"]
    try:
        d = happy_dossier()
        d["decision"].update(recommended_play="csm_checkin", customer_visible=True)
        p2 = only(ev.evaluate(d), "§8.2")
        assert len(p2) == 1 and p2[0]["severity"] == SEV_WEIGHT["critical"] and "mna_quiet_period" in p2[0]["explanation"]
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_P2_customer_visible_play_declared_not_visible_is_still_P2(ev):
    """docs/domain.md: every play except watch_only and renewal_risk_review is customer-visible. exec_escalation
    with customer_visible: false on a legal_hold account is a mislabelled outreach → P2."""
    ev.cx.accounts["acct_T"]["flags"] = ["legal_hold"]
    try:
        d = happy_dossier()
        d["decision"].update(recommended_play="exec_escalation", customer_visible=False)
        p2 = only(ev.evaluate(d), "§8.2")
        assert len(p2) == 1 and "exec_escalation" in p2[0]["explanation"]
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_P2_non_restricting_flags_do_not_fire(ev):
    """§8.2 restricts on two flags only: reference_customer / strategic / pilot / named_exec_sponsor with a
    customer-visible play → no P2."""
    ev.cx.accounts["acct_T"]["flags"] = ["reference_customer", "strategic", "pilot", "named_exec_sponsor"]
    try:
        d = happy_dossier()
        d["decision"].update(recommended_play="csm_checkin", customer_visible=True)
        assert "§8.2" not in rules(ev.evaluate(d))
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


def test_P2_on_a_suppressed_dossier_still_fires(ev):
    """§8.2 'A dossier on a restricted account with customer_visible: true is a critical violation' — the
    recommendation is the breach, whether or not anyone acted on it."""
    ev.cx.accounts["acct_T"]["flags"] = ["legal_hold"]
    try:
        d = suppressed(happy_dossier())
        d["decision"].update(recommended_play="csm_checkin", customer_visible=True)
        assert "§8.2" in rules(ev.evaluate(d))
    finally:
        ev.cx.accounts["acct_T"]["flags"] = []


# ── P5 (§8.5) ───────────────────────────────────────────────────────────────────────────────────

def test_P5_two_artefacts_of_the_same_source_type_is_one_source(ev):
    """§8.5 'at least two distinct sources' and the data dictionary: 'Distinct-source counts for the confidence
    rule use `source`'. Two support tickets → 1 distinct → P5 saying '1 distinct'."""
    ev.cx.artifacts["art_T2"] = dict(ARTIFACT, artifact_id="art_T2", text="Second ticket, same topic.")
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"].append({"step": 2, "artifact_id": "art_T2", "source": "support_ticket", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": "Second ticket, same topic."})
    try:
        p5 = only(ev.evaluate(d), "§8.5")
    finally:
        del ev.cx.artifacts["art_T2"]
    assert len(p5) == 1 and "1 distinct" in p5[0]["explanation"] and p5[0]["severity"] == SEV_WEIGHT["critical"]


def test_P5_high_confidence_with_zero_evidence(ev):
    """§8.5 with nothing attached: 0 distinct sources → P5."""
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"] = []
    d["actions"] = [a for a in d["actions"] if a["action"] != "attach_evidence"]
    p5 = only(ev.evaluate(d), "§8.5")
    assert len(p5) == 1 and "0 distinct" in p5[0]["explanation"]


def test_P5_second_source_fabricated_does_not_count(ev):
    """§8.5 counts evidence, and §7 I6 says a fabricated quote is not evidence: ticket + a crm_note whose quote is
    not in the artefact → 1 verified source → P5 alongside the I6."""
    ev.cx.artifacts["art_T2"] = dict(ARTIFACT, artifact_id="art_T2", type="crm_note", source="crm_note", text="Real note text.")
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"].append({"step": 2, "artifact_id": "art_T2", "source": "crm_note", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": "Words that are not in the note."})
    try:
        r = ev.evaluate(d)
    finally:
        del ev.cx.artifacts["art_T2"]
    assert {"§8.5", "I6"} <= rules(r)


# ── P7 (§8.7) ───────────────────────────────────────────────────────────────────────────────────

def test_P7_phone_number_in_routed_quote(ev):
    """§8.7 'email addresses or phone numbers'. A +49 phone in the quote of a routed dossier → P7 high 0.6."""
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text="Ring me on +49 30 1234 5678 if the sync fails again.")
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Ring me on +49 30 1234 5678 if the sync fails again."
    try:
        p7 = only(ev.evaluate(d), "§8.7")
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT
    assert len(p7) == 1 and p7[0]["step"] == 2 and p7[0]["severity"] == SEV_WEIGHT["critical"]


def test_P7_contact_details_in_a_suppressed_dossier_is_not_P7(ev):
    """§8.7 governs quotes 'carried into a routed dossier'; nobody read a suppressed one → no P7."""
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text="Call me. ada@example.com")
    d = suppressed(happy_dossier())
    d["evidence"][0]["quote"] = "Call me. ada@example.com"
    try:
        assert "§8.7" not in rules(ev.evaluate(d))
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_P7_invoice_id_and_iso_timestamp_in_routed_quote_are_not_contact_details(ev):
    """§8.7 is about contact details; 'INV-123456789' and an ISO timestamp are identifiers → no P7."""
    text = "Invoice INV-123456789 raised 2026-03-02T08:00:00Z is still open."
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, text=text)
    d = happy_dossier()
    d["evidence"][0]["quote"] = text
    try:
        assert "§8.7" not in rules(ev.evaluate(d))
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT


def test_P7_fires_on_the_cold_path_from_the_quote_alone():
    """Without load_context the quote is all there is; an email in a routed quote is still §8.7."""
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Reach me at ada@example.com"
    assert "§8.7" in rules(SignalEvaluator(labeller=None).evaluate(d))


# ── P3 (§8.3) ───────────────────────────────────────────────────────────────────────────────────

def test_P3_restricted_quote_reaching_a_human_by_preempt_only(ev):
    """§8.3 'routed to the account team': a human_preempt acknowledgement means a person is on it. The evaluator
    reads reached_human from the preempt (util.reached_human), so a restricted verbatim quote is P3 here too."""
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, restricted=True)
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:3] + [{"step": 3, "from_state": "hypothesis_formed", "to_state": "acknowledged",
                                           "at": "2026-03-02T11:00:00Z", "trigger": "human_preempt", "reason": ""}]
    d["actions"] = d["actions"][:1]
    d["notifications"] = []
    d["closed_at"] = "2026-03-02T11:00:00Z"
    try:
        r = ev.evaluate(d)
    finally:
        ev.cx.artifacts["art_T1"] = ARTIFACT
    assert "§8.3" in rules(r)


# ── P1: departure window boundaries (§8.1 bullet 4, 'within 90 days of renewal') ───────────────

DEPART = {"moving on": {"departure": 0.9}}


@pytest.mark.parametrize("dtr,expect_trigger", [(90, True), (91, False), (0, True), (-1, False)])
def test_P1_departure_window_boundaries(ev, dtr, expect_trigger):
    """'within 90 days' read as 0 ≤ days_to_renewal ≤ 90 (A5). At 90 the champion's own notice is a trigger and
    suppression is P1 1.0; at 91 or after the renewal date it is a recorded fact only."""
    art = dict(CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    r = case(ev, [art], DEPART, dtr=dtr)
    if expect_trigger:
        assert src(r)["confirmed"] == ["buyer_or_champion_departure"] and only(r, "§8.1")[0]["severity"] == 1.0
    else:
        assert r["_facts"]["triggers"] == [] and "§8.1" not in rules(r)
        assert "outside" in src(r)["per_artifact"]["art_T2"]["facts"]["buyer_or_champion_departure"]


def test_P1_departure_with_unknown_renewal_date_is_uncertain(ev):
    """§8.1 bullet 4 needs the renewal date; with neither renewal_date nor metadata.days_to_renewal the window
    cannot be applied → uncertain trigger, P1 at half weight when suppressed."""
    art = dict(CUSTOMER, author="Ada Lovelace", text="I am moving on at the end of the quarter.")
    d = suppressed(attach(happy_dossier(), art))
    d["metadata"].pop("days_to_renewal", None)
    r = case(ev, [art], DEPART, dossier=d)
    assert r["_facts"]["days_to_renewal"] is None
    assert src(r)["uncertain"] == ["buyer_or_champion_departure"] and src(r)["confirmed"] == []
    p1 = only(r, "§8.1")
    assert len(p1) == 1 and p1[0]["severity"] == pytest.approx(SEV_WEIGHT["critical"] * UNCERTAIN_FACTOR)


def test_P1_first_person_departure_by_a_non_champion_author_is_unattributed(ev):
    """§8.1 bullet 4 is about the economic buyer or named champion. 'I am moving on' from Grace (neither) resolves
    to nobody → unattributed, no trigger, no P1."""
    art = dict(CUSTOMER, author="Grace", text="I am moving on at the end of the quarter.")
    r = case(ev, [art], DEPART, dtr=60)
    assert r["_facts"]["triggers"] == [] and src(r)["unattributed"] == ["departure"] and "§8.1" not in rules(r)


def test_P1_departure_named_only_in_quoted_history_is_historical(ev):
    """The champion's name in a depth-1 quote is history (§4.2), not a current departure."""
    art = dict(CUSTOMER, text="Thanks, all sorted.\n\nOn 1 Jan 2026, Grace wrote:\n> Ada Lovelace is moving on next month.")
    r = case(ev, [art], DEPART, dtr=60)
    assert r["_facts"]["triggers"] == [] and src(r)["historical"] == ["departure"] and "§8.1" not in rules(r)


# ── P1: billing amount location and structure (§8.1 bullet 5) ───────────────────────────────────

def _bill(text, subject=None, artifact_id="art_BILL"):
    return dict(ARTIFACT, artifact_id=artifact_id, type="billing_event", source="billing_event", author="billing-bot",
                author_type="bot", subject=subject, text=text)


def test_P1_billing_amount_only_in_the_subject_is_read(ev):
    """docs/data_dictionary.md: the subject 'often carries the operative sentence'. Amount in the subject, none in
    the body: $20,000 = 20% of ARR → confirmed billing_dispute, P1 1.0 when suppressed."""
    bill = _bill("Status: disputed by customer AP.", subject="Invoice INV-9 for $20,000 disputed")
    r = case(ev, [bill], {}, detector="billing_dispute")
    assert src(r)["confirmed"] == ["billing_dispute"] and only(r, "§8.1")[0]["severity"] == 1.0


def test_P1_billing_detector_with_no_billing_artefact_attached_is_uncertain(ev):
    """spec §3.1: the billing_dispute detector fires on a disputed/overdue billing event; when no billing record is
    in the evidence the 5% test cannot run → uncertain 'amount unverified', P1 at half weight when suppressed."""
    art = dict(CUSTOMER, text="Following up on the backfill plan.")
    r = case(ev, [art], {}, detector="billing_dispute")
    assert src(r)["uncertain"] == ["billing_dispute(amount unverified)"]
    p1 = only(r, "§8.1")
    assert len(p1) == 1 and p1[0]["severity"] == pytest.approx(SEV_WEIGHT["critical"] * UNCERTAIN_FACTOR)


def test_P1_billing_amount_with_k_suffix_and_dollar_sign(ev):
    """util.money accepts '$6k' → 6,000 = 6% of ARR → confirmed (above 5%)."""
    r = case(ev, [_bill("Invoice INV-9 for $6k issued. Status: disputed by customer AP.")], {}, detector="billing_dispute")
    assert src(r)["confirmed"] == ["billing_dispute"]


def test_P1_billing_first_amount_in_the_block_decides(ev):
    """A7: one billing_event is one block; util.money reads the FIRST amount. '$1,000 late fee' before the
    '$20,000 invoice' → the read amount is 1,000 (1% of ARR) → no trigger. This pins a documented limit of the
    structural read so a change in it is deliberate."""
    r = case(ev, [_bill("A $1,000 late fee applies. Invoice INV-9 for $20,000 disputed by customer AP.")], {}, detector="billing_dispute")
    assert src(r)["confirmed"] == [] and "1,000" in src(r)["per_artifact"]["art_BILL"]["facts"]["billing"]


def test_P1_billing_dispute_paid_status_with_detector_still_reads_amount(ev):
    """A1: the detector says disputed (spec §3.1) even if the bot text reads 'paid'; the structural read follows
    the detector. $20,000 → confirmed."""
    r = case(ev, [_bill("Invoice INV-9 for $20,000 issued. Status: paid.")], {"paid": {"billing_dispute": 0.1}}, detector="billing_dispute")
    assert src(r)["confirmed"] == ["billing_dispute"]


# ── P1: routing state ───────────────────────────────────────────────────────────────────────────

def test_P1_trigger_honoured_by_human_preempt_is_not_P1(ev):
    """§8.1 'must put a human on the signal': a human_preempt acknowledgement is a human on it (§4.4)."""
    art = dict(CUSTOMER, text="We will not be renewing.")
    d = attach(happy_dossier(), art)
    d["lifecycle"] = d["lifecycle"][:3] + [{"step": 3, "from_state": "hypothesis_formed", "to_state": "acknowledged",
                                           "at": "2026-03-02T11:00:00Z", "trigger": "human_preempt", "reason": ""}]
    d["actions"] = d["actions"][:1]
    d["notifications"] = []
    d["closed_at"] = "2026-03-02T11:00:00Z"
    r = case(ev, [art], {"not be renewing": {"cancel_intent": 0.9}}, dossier=d)
    assert src(r)["confirmed"] == ["cancel_intent"] and "§8.1" not in rules(r) and r["_facts"]["reached_human"] is True


def test_P1_trigger_on_a_still_open_signal_inside_its_TTA_is_not_yet_a_missed_route(ev):
    """§8.1 says the agent 'must put a human on the signal'; §6.3 gives it 24h (P1) to do so. A signal cut at
    corroborating 2h after opening with a confirmed cancel trigger has not yet failed either duty: no suppression,
    the TTA clock is still running. Reading P1 here would flag every in-flight dossier."""
    art = dict(CUSTOMER, text="We will not be renewing.")
    d = attach(happy_dossier(), art)
    d["lifecycle"] = d["lifecycle"][:2]
    d["actions"] = d["actions"][:1]
    d["notifications"] = []
    d["closed_at"] = None
    d["decision"] = {"disposition": None, "recommended_play": None, "customer_visible": False, "reason": None}
    r = case(ev, [art], {"not be renewing": {"cancel_intent": 0.9}}, dossier=d)
    assert src(r)["confirmed"] == ["cancel_intent"]
    assert "§8.1" not in rules(r)


def test_P1_on_the_cold_path_from_a_quote_is_uncertain():
    """A8: without load_context the author is unknown, so a quote reading as cancel intent is an uncertain trigger;
    suppressed → P1 at half weight."""
    from signal_eval.labellers import TableLabeller
    e = SignalEvaluator(labeller=TableLabeller({"not be renewing": {"cancel_intent": 0.9}}))
    d = suppressed(happy_dossier())
    d["evidence"][0]["quote"] = "We will not be renewing."
    r = e.explain(d)
    assert r["_facts"]["trigger_source"]["uncertain"] == ["cancel_intent"]
    p1 = only(r, "§8.1")
    assert len(p1) == 1 and p1[0]["severity"] == pytest.approx(SEV_WEIGHT["critical"] * UNCERTAIN_FACTOR)


def test_P1_legal_reference_by_customer_at_depth_zero_with_bot_forward_below(ev):
    """Bullet 2 with a forward: the customer's own line 'our counsel will be in touch' at depth 0 is confirmed even
    though the depth-1 block is a bot digest; the forward is not masked because no other account is named."""
    art = dict(CUSTOMER, text="Our counsel will be in touch about the SLA.\n\n---------- Forwarded message ----------\nFrom: alerts\nSent: Monday\nDeploy finished.")
    r = case(ev, [art], {"counsel": {"legal_reference": 0.9}})
    assert src(r)["confirmed"] == ["legal_reference"] and only(r, "§8.1")[0]["severity"] == 1.0
