"""
ABOUTME: Known-answer tests for evidence currency (quote location vs quoted history), near-verbatim
ABOUTME: handling, P3/P5 policy conditions and M3/M4 "routed" semantics. No NLI model ever runs here.
"""

from conftest import ARTIFACT, SignalEvaluator, explain, happy_dossier, rules

THREAD = ("Sorted, thanks. Ignore the thread below.\n\n"
          "On 20 Sep 2025, Kenji Iyer wrote:\n"
          "> Third time this week, we are done with this vendor.")
HEAD_QUOTE = "Sorted, thanks. Ignore the thread below."
TAIL_QUOTE = "Third time this week, we are done with this vendor."


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def facts(r):
    return r["_facts"]["evidence"][0]


def synthetic_label(**over):
    """Label dict in the shape classifier.assemble builds; every trigger/topic off unless overridden."""
    lab = {"unverifiable": False, "n_chunks": 1, "scores": {}, "quoted_history": False, "quote_marker": None,
           "cancel_intent": False, "legal_reference": False, "security_incident": False, "departure": False,
           "billing_dispute": False, "sarcasm": False, "topic": None, "triggers_belong_elsewhere": False}
    lab.update(over)
    return lab


def with_artifact(ev, **over):
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, **over)


def suppressed_dossier():
    """Scored then suppressed; no notification, no scored→routed edge — no human reached."""
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": "immaterial"}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    return d


def routed_then_expired_dossier():
    """Went scored→routed, then timed out: a human was reached, final disposition is expired."""
    d = happy_dossier()
    d["lifecycle"][-1] = {"step": 7, "from_state": "routed", "to_state": "expired", "at": "2026-03-20T12:00:00Z", "trigger": "staleness_timeout", "reason": ""}
    d["decision"]["disposition"] = "expired"
    return d


# ── is_stale ─────────────────────────────────────────────────────────────────
def test_is_stale_by_quote_location_not_by_label():
    from signal_eval.classifier import is_stale
    hist = synthetic_label(quoted_history=True)
    assert is_stale(hist, "customer") is False                        # informational only
    assert is_stale(hist, "customer", quote_in_tail=True) is True
    assert is_stale({"unverifiable": True}, "customer", quote_in_tail=True) is True   # structural, no model needed
    assert is_stale(synthetic_label(sarcasm=True), "customer") is True
    assert is_stale(synthetic_label(sarcasm=True), None) is True
    assert is_stale(synthetic_label(sarcasm=True), "internal") is False
    assert is_stale(None, "customer") is False


# ── evidence: quote location ─────────────────────────────────────────────────
def test_quote_from_quoted_history_is_stale(ev):
    with_artifact(ev, text=THREAD)
    d = happy_dossier()
    d["evidence"][0]["quote"] = TAIL_QUOTE
    r = explain(ev, d)
    f = facts(r)
    assert f["status"] == "stale" and f["quote_location"] == "tail"
    q4 = only(r, "Q4")
    assert q4 and q4[0]["step"] == 2 and "quoted" in q4[0]["explanation"] and "art_T1" in q4[0]["explanation"]
    assert "I6" not in rules(r)
    assert r["_facts"]["n_stale_evidence"] == 1 and r["_facts"]["verified_sources"] == []
    assert r["_facts"]["has_attached_text"] is False


def test_quote_from_head_is_current_even_with_quoted_tail(ev):
    with_artifact(ev, text=THREAD)
    d = happy_dossier()
    d["evidence"][0]["quote"] = HEAD_QUOTE
    r = explain(ev, d)
    f = facts(r)
    assert f["status"] == "verified" and f["quote_location"] == "head"
    assert "Q4" not in rules(r) and r["_facts"]["verified_sources"] == ["support_ticket"]


def test_quote_in_both_head_and_tail_is_current(ev):
    with_artifact(ev, text=f"{TAIL_QUOTE}\n\nOn 20 Sep 2025, Kenji Iyer wrote:\n> {TAIL_QUOTE}")
    d = happy_dossier()
    d["evidence"][0]["quote"] = TAIL_QUOTE
    r = explain(ev, d)
    assert facts(r)["quote_location"] == "both" and facts(r)["status"] == "verified"
    assert "Q4" not in rules(r)


def test_quote_from_artifact_without_history_is_head(ev):
    r = explain(ev, happy_dossier())
    assert facts(r)["quote_location"] == "head" and facts(r)["status"] == "verified"


def test_near_verbatim_quote_is_diagnostic_only_and_not_verified(ev):
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We are planning a backfill of about  90M rows."   # extra space
    r = explain(ev, d)
    i6 = only(r, "I6")
    assert len(i6) == 1 and i6[0]["severity"] == 0.3 and i6[0]["step"] == 2 and "normalisation" in i6[0]["explanation"]
    assert facts(r)["status"] == "near_verbatim" and facts(r)["quote_location"] is None
    assert r["_facts"]["verified_sources"] == [] and r["_facts"]["has_attached_text"] is False


def test_verbatim_quote_is_verified_and_not_I6(ev):
    r = explain(ev, happy_dossier())
    assert "I6" not in rules(r) and r["_facts"]["has_attached_text"] is True


# ── evidence: sarcasm via synthetic labels (model never runs) ────────────────
def _activate_labels(ev, monkeypatch, label):
    monkeypatch.setattr(ev.cx, "label_artifact", lambda art: dict(label))
    monkeypatch.setattr(ev.cx, "use_classifier", True)
    monkeypatch.setattr(ev.cx, "classifier_active", True)


def test_customer_sarcasm_is_stale(ev, monkeypatch):
    _activate_labels(ev, monkeypatch, synthetic_label(sarcasm=True))
    r = explain(ev, happy_dossier())
    assert facts(r)["status"] == "stale" and facts(r)["quote_location"] == "head"
    q4 = only(r, "Q4")
    assert q4 and "sarcasm" in q4[0]["explanation"]


def test_internal_author_sarcasm_is_not_stale(ev, monkeypatch):
    with_artifact(ev, author_type="internal")
    _activate_labels(ev, monkeypatch, synthetic_label(sarcasm=True))
    r = explain(ev, happy_dossier())
    assert facts(r)["status"] == "verified" and "Q4" not in rules(r)


def test_quoted_history_label_alone_does_not_make_head_quote_stale(ev, monkeypatch):
    with_artifact(ev, text=THREAD)
    _activate_labels(ev, monkeypatch, synthetic_label(quoted_history=True, quote_marker="(?m)^On "))
    d = happy_dossier()
    d["evidence"][0]["quote"] = HEAD_QUOTE
    r = explain(ev, d)
    assert facts(r)["status"] == "verified" and facts(r)["labels"]["quoted_history"] is True
    assert "Q4" not in rules(r)


# ── P3 ───────────────────────────────────────────────────────────────────────
def test_restricted_verbatim_quote_reaching_human_is_critical_P3(ev):
    with_artifact(ev, restricted=True)
    r = ev.evaluate(happy_dossier())
    p3 = only(r, "P3")
    assert len(p3) == 1 and p3[0]["severity"] == 1.0 and p3[0]["step"] == 2
    assert "art_T1" in p3[0]["explanation"] and "verbatim" in p3[0]["explanation"]


def test_restricted_quote_not_reaching_human_is_partial_P3(ev):
    with_artifact(ev, restricted=True)
    r = ev.evaluate(suppressed_dossier())
    p3 = only(r, "P3")
    assert len(p3) == 1 and p3[0]["severity"] == 0.3 and "no human" in p3[0]["explanation"]


def test_restricted_routed_then_expired_is_still_critical_P3(ev):
    with_artifact(ev, restricted=True)
    r = ev.evaluate(routed_then_expired_dossier())
    assert only(r, "P3")[0]["severity"] == 1.0


def test_quote_absent_from_restricted_artifact_is_I6_not_P3(ev):
    with_artifact(ev, restricted=True)
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Nothing like this appears in the artefact."
    r = ev.evaluate(d)
    assert "P3" not in rules(r) and any(v["severity"] == 1.0 for v in only(r, "I6"))


def test_near_verbatim_quote_from_restricted_artifact_is_not_P3(ev):
    with_artifact(ev, restricted=True)
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We are planning a backfill of about  90M rows."
    r = ev.evaluate(d)
    assert "P3" not in rules(r) and only(r, "I6")[0]["severity"] == 0.3


def test_restricted_artifact_with_empty_quote_is_not_P3(ev):
    with_artifact(ev, restricted=True)
    d = happy_dossier()
    d["evidence"][0]["quote"] = ""
    assert "P3" not in rules(ev.evaluate(d))


def test_cold_path_P3_falls_back_to_dossier_restricted_flag():
    d = happy_dossier()
    d["evidence"][0]["restricted"] = True
    r = SignalEvaluator(use_classifier=False).evaluate(d)
    assert only(r, "P3")[0]["severity"] == 1.0
    d["evidence"][0]["restricted"] = False
    assert "P3" not in rules(SignalEvaluator(use_classifier=False).evaluate(d))


# ── P5 ───────────────────────────────────────────────────────────────────────
def test_high_hypothesis_confidence_with_one_source_is_P5(ev):
    d = happy_dossier()
    d["hypotheses"][0]["confidence"] = "high"          # scoring stays medium
    r = ev.evaluate(d)
    p5 = only(r, "P5")
    assert len(p5) == 1 and p5[0]["step"] == 2 and "1 distinct" in p5[0]["explanation"]


def test_medium_confidence_everywhere_is_not_P5(ev):
    assert "P5" not in rules(ev.evaluate(happy_dossier()))


def test_high_confidence_with_two_verified_sources_is_not_P5(ev):
    ev.cx.artifacts["art_T2"] = dict(ARTIFACT, artifact_id="art_T2", type="crm_note", source="crm_note",
                                     text="Alan confirmed the budget freeze on the call.")
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"].append({"step": 2, "artifact_id": "art_T2", "source": "crm_note", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": "Alan confirmed the budget freeze on the call."})
    r = explain(ev, d)
    assert "P5" not in rules(r) and r["_facts"]["verified_sources"] == ["crm_note", "support_ticket"]


def test_high_confidence_second_source_from_quoted_history_is_P5(ev):
    ev.cx.artifacts["art_T2"] = dict(ARTIFACT, artifact_id="art_T2", type="crm_note", source="crm_note", text=THREAD)
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"].append({"step": 2, "artifact_id": "art_T2", "source": "crm_note", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": TAIL_QUOTE})
    r = explain(ev, d)
    assert "P5" in rules(r) and r["_facts"]["verified_sources"] == ["support_ticket"]


def test_high_confidence_second_source_bot_alert_is_P5(ev):
    ev.cx.artifacts["art_BOT"] = dict(ARTIFACT, artifact_id="art_BOT", type="bot_alert", source="bot_alert",
                                      author_type="bot", text="Deploy finished. Auto-resolved.")
    d = happy_dossier()
    d["scoring"]["confidence"] = "high"
    d["evidence"].append({"step": 2, "artifact_id": "art_BOT", "source": "bot_alert", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": "Deploy finished. Auto-resolved."})
    assert "P5" in rules(ev.evaluate(d))


def test_cold_path_P5_counts_attached_non_bot_sources():
    d = happy_dossier()
    d["hypotheses"][0]["confidence"] = "high"
    assert "P5" in rules(SignalEvaluator(use_classifier=False).evaluate(d))
    d["evidence"].append({"step": 2, "artifact_id": "art_X", "source": "crm_note", "restricted": False,
                          "attached_at": "2026-03-02T09:40:00Z", "quote": "anything"})
    assert "P5" not in rules(SignalEvaluator(use_classifier=False).evaluate(d))


# ── M3 / M4 ──────────────────────────────────────────────────────────────────
def test_routed_then_expired_below_floor_is_M4(ev):
    d = routed_then_expired_dossier()
    d["scoring"]["arr_at_risk"] = 5_000
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    r = ev.evaluate(d)
    m4 = only(r, "M4")
    assert len(m4) == 1 and m4[0]["step"] == 5 and "5,000" in m4[0]["explanation"] and "18,000" in m4[0]["explanation"]


def test_suppressed_below_floor_never_routed_is_not_M4(ev):
    d = suppressed_dossier()
    d["scoring"]["arr_at_risk"] = 5_000
    d["actions"][2]["params"]["arr_at_risk"] = 5_000
    assert not {"M3", "M4"} & rules(ev.evaluate(d))


def test_routed_then_expired_above_arr_is_M1_and_M3(ev):
    d = routed_then_expired_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = ev.evaluate(d)
    assert {"M1", "M3"} <= rules(r) and only(r, "M3")[0]["severity"] == 0.3


def test_suppressed_above_arr_is_M1_only(ev):
    d = suppressed_dossier()
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = ev.evaluate(d)
    assert "M1" in rules(r) and "M3" not in rules(r)
