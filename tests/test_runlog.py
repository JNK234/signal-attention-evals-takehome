"""
ABOUTME: Tests for the evaluate()/explain() contract split and the run persistence layer (runlog):
ABOUTME: evaluate() returns exactly the README's four keys; explain() adds _facts; save_run/load_run round-trip.
"""

import json

from conftest import explain, happy_dossier
from signal_eval.runlog import load_run, save_run

CONTRACT_KEYS = {"quality_score", "risk_score", "deserved_attention", "violations"}


def test_evaluate_returns_exactly_four_keys(ev):
    """README §1: the grader reads exactly these four keys; nothing else may leak into the contract."""
    assert set(ev.evaluate(happy_dossier())) == CONTRACT_KEYS


def test_explain_returns_result_plus_facts(ev):
    d = happy_dossier()
    r = explain(ev, d)
    assert set(r) == CONTRACT_KEYS | {"_facts"}
    assert isinstance(r["_facts"], dict) and r["_facts"]["context_loaded"] is True
    assert {k: r[k] for k in CONTRACT_KEYS} == ev.evaluate(d)


def test_save_run_writes_meta_then_rows(ev, tmp_path):
    d = happy_dossier()
    r = explain(ev, d)
    rows = [{"signal_id": d["signal_id"], "result": {k: r[k] for k in CONTRACT_KEYS}, "facts": r["_facts"]}]
    path = tmp_path / "run.jsonl"
    meta = save_run(path, rows, {"model_id": "m", "classifier_reason": "disabled", "thresholds": {"nli": 0.5}})
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    head = json.loads(lines[0])
    assert set(head) == {"_meta"}
    assert set(head["_meta"]) >= {"created", "git_sha", "model_id", "classifier_reason", "thresholds", "n"}
    assert head["_meta"]["n"] == 1 and head["_meta"]["model_id"] == "m" and meta == head["_meta"]
    assert json.loads(lines[1]) == rows[0]
    meta2, rows2 = load_run(path)
    assert meta2 == head["_meta"] and rows2 == rows
