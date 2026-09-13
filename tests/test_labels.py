"""
ABOUTME: Known-answer tests for the labeller package: three-way decisions, block-level readings, the
ABOUTME: cache key, the TableLabeller test double and resolve() — no NLI model ever runs here.
"""

import json

import pytest

from conftest import ACCOUNT, ARTIFACT, SignalEvaluator, with_labels
from signal_eval import labels
from signal_eval.labellers import resolve
from signal_eval.labellers.base import TableLabeller
from signal_eval.labellers.cache import CachedLabeller, LabelCache
from signal_eval.labellers.nli import MODEL_ID
from signal_eval.labels import KNOWN_CASES, decide, filled_hypotheses, text_key

THREAD = ("Sorted, thanks. Ignore the thread below, that was back in Q1.\n\nOn 20 Sep 2025, Kenji Iyer wrote:\n"
          "> Third time this week. Loads for the exec dashboard are taking 60s or just spinning.\n\n"
          "Best,\nKenji Iyer\nkenji@ravensworth.com | +1-415-645-1761")


def reader(table, **art):
    ev = SignalEvaluator(labeller=TableLabeller(table))
    ev.cx.accounts = {ACCOUNT["account_id"]: ACCOUNT}
    return ev.cx.read_artifact(dict(ARTIFACT, **art))


# ── 1. decide is three-way ──────────────────────────────────────────────────
def test_decide_three_way():
    low, high = labels.THRESHOLDS[MODEL_ID]["cancel_intent"]
    assert low < high
    assert decide(high, "cancel_intent", MODEL_ID) is True
    assert decide(low, "cancel_intent", MODEL_ID) is False
    assert decide((low + high) / 2, "cancel_intent", MODEL_ID) is None
    assert decide(None, "cancel_intent", MODEL_ID) is None
    assert decide(0.9, "cancel_intent", "unknown/model") is True      # default band for an uncalibrated model
    assert decide(0.5, "cancel_intent", "unknown/model") is None


def test_threshold_table_is_well_formed():
    """Every label has a band for the model, low < high, and no band lets True below 0.35 or False above 0.65
    (the calibration guard in analysis/calibrate_thresholds.py)."""
    table = labels.THRESHOLDS[MODEL_ID]
    assert set(table) == set(labels.LABEL_HYPOTHESES)
    for label, (low, high) in table.items():
        assert 0.0 <= low < high <= 1.0, label
        assert high >= labels.DEFAULT_BAND[0] and low <= labels.DEFAULT_BAND[1], label


# ── 2. unreadable block → verdict None, adapter says unverifiable ───────────
def test_unreadable_block_is_none_everywhere():
    ev = SignalEvaluator(labeller=TableLabeller({}, unreadable={"garbled"}))
    r = ev.cx.read_artifact(dict(ARTIFACT, text="garbled bytes here"))
    assert r["unreadable"] == [0] and all(v is None for v in r["verdict"].values())
    assert ev.cx.label_artifact(dict(ARTIFACT, text="garbled bytes here"))["unverifiable"] is True


# ── 3. TableLabeller: first matching substring, label name → score ──────────
def test_table_labeller_maps_label_names_to_sentences():
    lab = TableLabeller({"renew": {"cancel_intent": 0.9}, "budget": {"topic:budget_pressure": 0.8}})
    sentences = [s for ss in filled_hypotheses(ACCOUNT).values() for s in ss]
    res = lab.label(["we will not renew", "budget cut", "hello"], sentences)
    assert res.readable == [True, True, True]
    cancel = [s for s in filled_hypotheses(ACCOUNT)["cancel_intent"]]
    assert all(res.scores[0][s] == 0.9 for s in cancel)
    assert res.scores[1][filled_hypotheses(ACCOUNT)["topic:budget_pressure"][0]] == 0.8
    assert res.scores[2][cancel[0]] == 0.0
    assert res.scores[0][filled_hypotheses(ACCOUNT)["departure"][1]] == 0.0   # the {names} sentence resolves too


# ── 4. cache key includes text, label sentence and model id ─────────────────
def test_cache_key_and_file_format(tmp_path):
    k = text_key("t", "s", "m")
    assert k != text_key("t", "s", "m2") and k != text_key("t", "s2", "m") and k != text_key("t2", "s", "m")
    path = tmp_path / "labels_x.json"
    c = LabelCache(path)
    c.put_many(["t"], ["s"], [{"s": 0.4}], "m")
    assert c.get_many(["t"], ["s"], "m") == [{"s": 0.4}] and c.get_many(["t"], ["s"], "other") == [{}]
    c.flush()
    data = json.loads(path.read_text())
    assert data["_meta"] == {"format": "block-label-v3", "model_id": "m"} and data[k] == 0.4
    assert LabelCache(path).get_many(["t"], ["s"], "m") == [{"s": 0.4}]


# ── 5. verdict from depth 0 only; history recorded ──────────────────────────
def test_verdict_from_depth_zero_history_recorded():
    r = reader({"Third time": {"topic:reliability_erosion": 0.9, "cancel_intent": 0.9}}, text=THREAD)
    assert [b.depth for b in r["blocks"] if not b.is_signature] == [0, 1]
    assert r["verdict"]["topic:reliability_erosion"] is False and r["verdict"]["cancel_intent"] is False
    assert r["historical"] == {"topic:reliability_erosion", "cancel_intent"}
    assert r["score"]["cancel_intent"] == 0.0 and "Sorted" in r["best"]["cancel_intent"]
    assert r["other_account"] is None and r["truncated"] is False


# ── 6. block context: a self-defusing next sentence stays in the same document ─
@pytest.mark.parametrize("text,expect", [(t, e) for t, lab, e in KNOWN_CASES if lab == "cancel_intent"])
def test_block_context_pins(text, expect):
    """The two pins from the plan. The invariant under test is structural — one sentence-free block — and
    the TableLabeller supplies the meaning a model is expected to read (WP-D checks the real model)."""
    r = reader({"not to.": {"cancel_intent": 0.1}, "not be renewing": {"cancel_intent": 0.9}}, subject=None, text=text)
    assert len(r["blocks"]) == 1 and r["blocks"][0].text == text
    assert r["verdict"]["cancel_intent"] is expect


# ── 7. bot artefacts read billing only ──────────────────────────────────────
def test_bot_reads_billing_only():
    r = reader({"disputed": {"billing_dispute": 0.9, "cancel_intent": 0.9, "legal_reference": 0.9}},
               author_type="bot", type="billing_event", source="billing_event",
               text="Invoice INV-9 for $9,000. Status: disputed by customer AP.")
    assert r["verdict"]["billing_dispute"] is True
    assert r["verdict"]["cancel_intent"] is None and r["verdict"]["legal_reference"] is None
    assert set(r["score"]) == {"billing_dispute"}
    lab = SignalEvaluator(labeller=TableLabeller({"disputed": {"billing_dispute": 0.9}})).cx.label_artifact(
        dict(ARTIFACT, author_type="bot", text="Status: disputed"))
    assert lab["billing_dispute"] is True and lab["cancel_intent"] is False and lab["unverifiable"] is False


# ── 8. resolve ──────────────────────────────────────────────────────────────
def test_resolve_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert resolve(None, None) == (None, "disabled")
    lab, why = resolve("auto", tmp_path / "labels.json")
    assert lab is None and why == "auto: no cache and no local model"
    lab, why = resolve(TableLabeller({}), None)
    assert isinstance(lab, CachedLabeller) and lab.model_id == "table" and why == "explicit"
    cache = tmp_path / "labels.json"
    cache.write_text(json.dumps({"_meta": {"format": "block-label-v3", "model_id": MODEL_ID}, "k": 0.5}))
    lab, why = resolve("auto", cache)
    assert lab is not None and why == "cache"
    assert lab.label(["never seen"], ["s"]).readable == [False]


# ── other_account normalisation and with_labels ────────────────────────────
@pytest.mark.parametrize("raw,expect", [("Brightwater Systems", "Brightwater Systems"), ("", None), (None, None),
                                        (True, "<unnamed>"), (["x"], "<unnamed>")])
def test_other_account_is_normalised(raw, expect):
    assert reader({}, mentions_other_account=raw)["other_account"] == expect


def test_with_labels_swaps_the_table_and_resets_the_cache(ev):
    assert ev.cx.read_artifact(ARTIFACT)["verdict"]["cancel_intent"] is False
    with_labels(ev, {"backfill": {"cancel_intent": 0.9}})
    assert ev.cx.read_artifact(ARTIFACT)["verdict"]["cancel_intent"] is True
    assert labels.LABEL_SET_VERSION == "v5-blocks"
