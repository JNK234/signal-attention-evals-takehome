"""
ABOUTME: Adversarial sweep of the reading layer and evidence rules (spec §7 I6, §4.2, §10 Q2–Q4, decision 7):
ABOUTME: quotes across block boundaries, signature-only bodies, empty quotes, truncation, cold-path honesty.
"""

import pytest

from conftest import ARTIFACT, SignalEvaluator, explain, happy_dossier, rules, with_labels
from signal_eval.labellers import TableLabeller
from signal_eval.spec import SEV_WEIGHT
from signal_eval.text import MAX_BLOCK_CHARS, blocks, find_quote
from signal_eval.util import money


def only(r, rule):
    return [v for v in r["violations"] if v["rule"] == rule]


def with_artifact(ev, **over):
    ev.cx.artifacts["art_T1"] = dict(ARTIFACT, **over)


def facts(r):
    return r["_facts"]["evidence"][0]


# ── I6: where a verbatim quote may sit ──────────────────────────────────────────────────────────

def test_quote_spanning_body_and_signature_is_current_evidence(ev):
    """A quote that runs from the last body line into the closing ('Body line.\\n\\nBest,') is still the current
    message (text.find_quote falls back to the joined depth-0 text) → verified, location head, no I6/Q4."""
    with_artifact(ev, text="Body line.\n\nBest,\nAda\nada@example.com")
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Body line.\n\nBest,"
    r = explain(ev, d)
    assert facts(r)["status"] == "verified" and facts(r)["quote_location"] == "head"
    assert not {"I6", "Q4"} & rules(r)


def test_quote_that_is_only_the_subject_is_verified_and_current(ev):
    """docs/data_dictionary.md: the subject 'often carries the operative sentence'. A quote equal to the subject
    is verbatim (I6) and sits in the depth-0 block → verified / head."""
    with_artifact(ev, subject="Notice of non-renewal", text="Details in the attached letter.")
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Notice of non-renewal"
    r = explain(ev, d)
    assert facts(r)["status"] == "verified" and facts(r)["quote_location"] == "head" and "I6" not in rules(r)


def test_quote_spanning_subject_and_first_body_line_is_verified(ev):
    """The subject is joined to the body's first depth-0 block; a quote across the join is verbatim in the
    document and current."""
    with_artifact(ev, subject="Question", text="We are planning a backfill of about 90M rows.")
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Question\nWe are planning a backfill"
    r = explain(ev, d)
    assert facts(r)["status"] == "verified" and facts(r)["quote_location"] == "head"


def test_quote_crossing_from_current_text_into_quoted_history(ev):
    """A quote that starts in the current message and runs into the '> ' history is verbatim in the document
    but sits in no single block. spec §7 I6 is satisfied (verbatim); §4.2 makes the tail history. The evaluator
    must not call it fabricated; it records location None and treats it as verified — pinned so a change is
    deliberate."""
    text = "Sorted now.\n\nOn 1 Mar 2026, Ben wrote:\n> we are done with this vendor"
    with_artifact(ev, text=text)
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Sorted now.\n\nOn 1 Mar 2026, Ben wrote:\n> we are done"
    r = explain(ev, d)
    assert "I6" not in rules(r) and facts(r)["quote_location"] is None and facts(r)["status"] == "verified"


def test_empty_quote_on_a_real_same_account_artefact_is_not_I6(ev):
    """§7 I6's three tests: exists, same account, verbatim. An empty quote trivially appears; the entry is not a
    fabrication. It is recorded as verified with location None (nothing to locate)."""
    d = happy_dossier()
    d["evidence"][0]["quote"] = ""
    r = explain(ev, d)
    assert "I6" not in rules(r) and facts(r)["status"] == "verified" and facts(r)["quote_location"] is None


def test_zero_evidence_has_no_evidence_findings(ev):
    """No evidence entries → no I6/P4/P3/P7/Q4-attach and nothing verified; the hypothesis is then unsupported
    (Q2) and the UNEVALUATED entry has nothing to count."""
    d = happy_dossier()
    d["evidence"] = []
    d["actions"] = [a for a in d["actions"] if a["action"] != "attach_evidence"]
    r = explain(ev, d)
    assert not {"I6", "P4", "P3", "P7", "Q4", "UNEVALUATED"} & rules(r)
    assert r["_facts"]["verified_sources"] == [] and r["_facts"]["has_attached_text"] is False


def test_quote_matching_only_case_insensitively_is_near_verbatim(ev):
    """§7 I6 'verbatim': a case change is not verbatim; util.norm lowercases, so it is the diagnostic near match
    (uncertain I6 0.5), not a clean pass and not a fabrication."""
    d = happy_dossier()
    d["evidence"][0]["quote"] = "we are planning a backfill of about 90m rows."
    i6 = only(ev.evaluate(d), "I6")
    assert len(i6) == 1 and i6[0]["severity"] == pytest.approx(SEV_WEIGHT["critical"] * 0.5) and "normalisation" in i6[0]["explanation"]


# ── block layer: signature-only bodies, truncation, money forms ─────────────────────────────────

def test_body_that_is_only_a_signature_has_no_content_block(ev):
    """'Best,\\nAda\\nada@example.com' is a closing plus a name plus a contact line: every block is a signature, there
    is nothing current to read. The artefact is still real (verified) and carries no trigger, no UNEVALUATED."""
    bs = blocks(None, "Best,\nAda\nada@example.com")
    assert bs and all(b.is_signature for b in bs)
    with_artifact(ev, text="Best,\nAda\nada@example.com")
    with_labels(ev, {"Ada": {"cancel_intent": 0.9}})
    d = happy_dossier()
    d["evidence"][0]["quote"] = "Best,"
    r = explain(ev, d)
    assert facts(r)["status"] == "verified" and r["_facts"]["triggers"] == [] and "UNEVALUATED" not in rules(r)


def test_block_longer_than_the_model_budget_is_flagged_truncated():
    """Plan decision 1: a block over MAX_BLOCK_CHARS is scored as one truncated document and flagged."""
    e = SignalEvaluator(labeller=TableLabeller({}))
    long = "word " * (MAX_BLOCK_CHARS // 4)
    r = e.cx.read_artifact(dict(ARTIFACT, text=long))
    assert r["truncated"] is True and len(r["blocks"]) == 1
    assert e.cx.read_artifact(ARTIFACT)["truncated"] is False


def test_find_quote_prefers_content_over_signature_for_head():
    """A quote that is only the signature line is 'head' via the joined depth-0 fallback (it is current text,
    just not content); a quote absent from every block is None."""
    bs = blocks(None, "Body.\n\nBest,\nAda")
    assert find_quote("Best,\nAda", bs) == "head"
    assert find_quote("Ada is leaving", bs) is None


@pytest.mark.parametrize("text,expect", [
    ("$5k", (5000.0, "USD")),
    ("$1.5M over the term", (1500000.0, "USD")),
    ("USD1,200", (1200.0, "USD")),
    ("5 USD", (5.0, "USD")),
    ("$ 300", (300.0, "USD")),
    ("$300k rows", (300000.0, "USD")),      # documented limit: a currency sign wins even when 'rows' follows
    ("300k rows", None),
    ("about $", None),
])
def test_money_prefix_and_suffix_forms(text, expect):
    assert money(text) == expect


# ── Q2 / I5 via the labeller (spec §10 Q2, §7 I5) ───────────────────────────────────────────────

def test_no_hypothesis_while_customer_text_reads_as_a_topic_is_I5(ev):
    """§7 I5: 'if the attached evidence clearly supports an explanation … but the signal is left as
    no_hypothesis, that is a potential misclassification'. product_gap read at 0.9 on the customer ticket →
    I5 medium 0.3 at the hypothesis step."""
    with_labels(ev, {"backfill": {"topic:product_gap": 0.9}})
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "no_hypothesis"
    i5 = only(ev.evaluate(d), "I5")
    assert len(i5) == 1 and i5[0]["step"] == 2 and "product_gap" in i5[0]["explanation"] and i5[0]["severity"] == SEV_WEIGHT["critical"]


def test_hypothesis_contradicted_by_the_evidence_topic_is_Q2(ev):
    """§10 Q2 'naming budget_pressure when the thread is plainly about a missing feature'. budget_pressure held,
    the only artefact reads product_gap 0.9 → Q2 naming both."""
    with_labels(ev, {"backfill": {"topic:product_gap": 0.9}})
    q2 = only(ev.evaluate(happy_dossier()), "Q2")
    assert any("budget_pressure" in v["explanation"] and "product_gap" in v["explanation"] for v in q2)


def test_hypothesis_matching_the_evidence_topic_has_no_Q2(ev):
    """Adjacent negative: budget_pressure held and read at 0.9 on the customer ticket → no Q2 at all."""
    with_labels(ev, {"backfill": {"topic:budget_pressure": 0.9}})
    assert "Q2" not in rules(ev.evaluate(happy_dossier()))


def test_hypothesis_resting_only_on_quoted_history_is_Q2(ev):
    """§4.2 + §10 Q2: the only evidence is a quote lifted from the '>' tail → stale (Q4) and the named cause rests
    on nothing current → Q2 'rests only on quoted-history'."""
    with_artifact(ev, text="Sorted, thanks.\n\nOn 1 Mar 2026, Ben wrote:\n> budget freeze across the org")
    d = happy_dossier()
    d["evidence"][0]["quote"] = "budget freeze across the org"
    r = ev.evaluate(d)
    assert any("quoted-history" in v["explanation"] for v in only(r, "Q2")) and "Q4" in rules(r)


def test_Q3_P1_severity_with_only_bot_alert_evidence(ev):
    """§10 Q3 'P1 on an account with no corroborating text at all is overconfident'. The only evidence is a
    bot_alert → no verified non-bot source → Q3."""
    with_artifact(ev, type="bot_alert", source="bot_alert", author_type="bot", text="Deploy finished. Auto-resolved.")
    d = happy_dossier()
    d["evidence"][0].update(source="bot_alert", quote="Deploy finished. Auto-resolved.")
    d["scoring"]["severity"] = "P1"
    d["actions"][2]["params"]["severity"] = "P1"
    q3 = only(ev.evaluate(d), "Q3")
    assert any("no verified non-bot text" in v["explanation"] for v in q3)


# ── cold path and honesty (decision 7) ──────────────────────────────────────────────────────────

def test_cold_path_without_labeller_on_a_dossier_with_quotes_is_UNEVALUATED():
    """No load_context and no labeller: the quote could not be read → exactly one UNEVALUATED entry, severity 0,
    and the four contract keys."""
    r = SignalEvaluator(labeller=None).explain(happy_dossier())
    meta = only(r, "UNEVALUATED")
    assert len(meta) == 1 and meta[0]["severity"] == 0.0 and "1 artefact" in meta[0]["explanation"]
    assert r["_facts"]["unevaluated"] == ["P1", "Q2", "I5", "Q4"]


def test_cold_path_skips_corpus_rules_but_still_runs_the_rest():
    """Without load_context I6/P4/M6/Q5 cannot run (no corpus); the lifecycle, timing and materiality rules do.
    A fabricated-looking quote is not I6 cold; a late notification is still T3."""
    d = happy_dossier()
    d["evidence"][0]["quote"] = "words that are in no artefact"
    d["notifications"][0]["at"] = "2026-03-06T14:00:00Z"
    d["actions"][3]["at"] = "2026-03-06T14:00:00Z"
    r = SignalEvaluator(labeller=None).evaluate(d)
    assert "I6" not in rules(r) and "T3" in rules(r)


def test_loaded_evaluator_on_an_account_absent_from_the_corpus_uses_metadata(ev):
    """A dossier for acct_UNKNOWN (not in accounts.jsonl) falls back to its metadata snapshot: ARR 100,000, so
    250,000 at risk is M1; the artefact lookup still runs (art_T1 belongs to acct_T → P4)."""
    d = happy_dossier()
    d["account_id"] = "acct_UNKNOWN"
    d["scoring"]["arr_at_risk"] = 250_000
    d["actions"][2]["params"]["arr_at_risk"] = 250_000
    r = explain(ev, d)
    assert {"M1", "P4"} <= rules(r) and r["_facts"]["errors"] == []


def test_evidence_entry_with_none_artifact_id_is_I6_not_a_crash(ev):
    """An evidence entry with artifact_id null cannot resolve → I6 ('does not exist'), no error record."""
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = None
    r = explain(ev, d)
    assert "I6" in rules(r) and r["_facts"]["errors"] == []


def test_lifecycle_entries_with_unparseable_timestamps_do_not_crash_and_actions_are_I4(ev):
    """Garbage `at` on every lifecycle entry: no exception, the edge checks still run (no TM on the happy path),
    and every edge action becomes I4 because it cannot be placed in time."""
    d = happy_dossier()
    for e in d["lifecycle"]:
        e["at"] = "not a time"
    r = explain(ev, d)
    assert r["_facts"]["errors"] == [] and "TM" not in rules(r)
    assert {v["step"] for v in only(r, "I4")} >= {3, 5, 6}
