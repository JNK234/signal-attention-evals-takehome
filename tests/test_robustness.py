"""
ABOUTME: Robustness tests for the SignalEvaluator contract (README l.218-226): evaluate() never raises,
ABOUTME: never loses findings, never touches the network, and says why the classifier is on or off.
"""

import json
import os
import sys

import pytest

from conftest import ACCOUNT, ARTIFACT, OTHER_ARTIFACT, OWNER, SignalEvaluator, TableLabeller, happy_dossier, rules, telemetry

import signal_eval.labellers as labellers_pkg
from signal_eval.labellers.base import LabelResult
from signal_eval.labellers.cache import CACHE_FLUSH_EVERY, CACHE_FORMAT
from signal_eval.labellers.nli import MODEL_ID

LIST_FIELDS = ("lifecycle", "actions", "evidence", "notifications", "hypotheses", "metrics_claimed")
CONTRACT_KEYS = {"quality_score", "risk_score", "deserved_attention", "violations"}


def stub_classifier(monkeypatch, available=True, error=None):
    """Replace the NLI engine with a recorder behind the labeller contract. `created` grows when the pipeline
    would be built (warm() / first label()), so a test can assert the model never loads at construction or
    inside evaluate(). The real model must never load in tests."""
    created = []

    class Stub:
        model_id = MODEL_ID

        def __init__(self, *a, **k):
            self.available = None
            self.error = None

        def warm(self):
            if self.available is None:
                created.append(self)
                self.available, self.error = available, error
            return self.available

        def label(self, texts, sentences):
            ok = bool(self.warm())
            return LabelResult([{s: 0.0 for s in sentences} for _ in texts], [ok] * len(texts))

    monkeypatch.setattr(labellers_pkg, "NLILabeller", Stub)
    return created


@pytest.fixture
def no_local_model(monkeypatch, tmp_path):
    """Point the HF hub cache at an empty dir so `auto` cannot see a model on this machine."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    return tmp_path


def fake_local_model(hub_dir):
    snap = hub_dir / f"models--{MODEL_ID.replace('/', '--')}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")


def isolated(tmp_path, **kw):
    """An evaluator whose score cache lives under tmp_path, so the repo's own cache cannot flip a test."""
    return SignalEvaluator(label_cache_path=tmp_path / "labels.json", **kw)


def input_errors(r):
    return [e for e in r["_facts"]["errors"] if e["check"] == "input"]


# ── 1. input types (README l.222: "must still return something sensible") ─────────────────────────
@pytest.mark.parametrize("bad", [None, "not a dossier", 42, ["a", "list"]])
def test_non_dict_dossier_is_treated_as_empty(ev, bad):
    r = ev.evaluate(bad)
    assert set(r) >= CONTRACT_KEYS
    assert len(input_errors(r)) == 1 and "dossier" in input_errors(r)[0]["error"]


def test_non_dict_metadata_is_treated_as_empty(ev):
    d = happy_dossier()
    d["metadata"] = ["not", "a", "dict"]
    r = ev.evaluate(d)
    assert set(r) >= CONTRACT_KEYS
    errs = input_errors(r)
    assert len(errs) == 1 and "metadata" in errs[0]["error"]
    assert [e for e in r["_facts"]["errors"] if e["check"] != "input"] == []


@pytest.mark.parametrize("field", LIST_FIELDS)
@pytest.mark.parametrize("bad", ["string", 7, {"step": 1}])
def test_non_list_field_is_treated_as_empty(ev, field, bad):
    d = happy_dossier()
    d[field] = bad
    r = ev.evaluate(d)
    assert set(r) >= CONTRACT_KEYS
    errs = input_errors(r)
    assert len(errs) == 1 and field in errs[0]["error"]
    # every other check still ran cleanly on the coerced dossier
    assert [e for e in r["_facts"]["errors"] if e["check"] != "input"] == []


def test_null_list_fields_are_not_input_errors(ev):
    d = happy_dossier()
    d["notifications"] = None
    assert input_errors(ev.evaluate(d)) == []


# ── 4. per-entry isolation (README l.218: evidence integrity per entry) ────────────────────────────
def test_malformed_evidence_entry_does_not_hide_fabrication_in_valid_entry(ev):
    d = happy_dossier()
    d["evidence"][0]["quote"] = "We are planning a backfill of about 180M rows."
    d["evidence"].append(None)
    r = ev.evaluate(d)
    assert "I6" in rules(r)
    errs = input_errors(r)
    assert len(errs) == 1 and "evidence[1]" in errs[0]["error"]
    assert [e for e in r["_facts"]["errors"] if e["check"] != "input"] == []


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_non_dict_entries_are_dropped_and_recorded(ev, field):
    d = happy_dossier()
    d[field] = list(d[field]) + [None, "junk", 3]
    r = ev.evaluate(d)
    errs = input_errors(r)
    assert len(errs) == 3 and all(field in e["error"] for e in errs)
    assert [e for e in r["_facts"]["errors"] if e["check"] != "input"] == []


def test_malformed_lifecycle_entry_keeps_other_findings(ev):
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_OTHER"     # P4 from the evidence check
    d["lifecycle"].insert(0, "garbage")                # would have thrown inside check_transitions
    r = ev.evaluate(d)
    assert "P4" in rules(r)
    assert len(input_errors(r)) == 1


# ── 2. label-cache persistence (README l.225-226: self-contained, side effects never break evaluate) ─
# Migrated to the labellers.cache API: the cache is keyed per (block, sentence, model) score and batches
# on new keys; the tests keep their intent (no write per dossier, flush forces, failure recorded not raised).
def test_cache_write_failure_is_recorded_not_raised(ev, monkeypatch, tmp_path):
    cache = tmp_path / "ro" / "labels.json"
    ev.cx.cache.path = cache
    ev.cx.cache.put_many(["k"], ["x"], [{"x": 0.1}], "table")
    ev.cx.cache._dirty = CACHE_FLUSH_EVERY
    monkeypatch.setattr(type(cache), "write_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("read-only")))
    d = happy_dossier()
    d["evidence"][0]["artifact_id"] = "art_NOPE"
    r = ev.evaluate(d)
    assert "I6" in rules(r)
    errs = [e for e in r["_facts"]["errors"] if e["check"] == "label_cache"]
    assert len(errs) == 1 and "PermissionError" in errs[0]["error"]
    assert not cache.exists()


def test_cache_is_written_in_batches_and_flush_forces_it(tmp_path):
    cache = tmp_path / "labels.json"
    e = SignalEvaluator(labeller=TableLabeller({}), label_cache_path=cache)
    n = CACHE_FLUSH_EVERY
    e.cx.cache.put_many([f"distinct text number {i}" for i in range(n - 1)], ["s"], [{"s": 0.0}] * (n - 1), "table")
    e.cx.save_cache()
    assert not cache.exists()
    e.cx.cache.put_many(["the last text of the batch"], ["s"], [{"s": 0.0}], "table")
    e.cx.save_cache()
    assert cache.exists() and len(json.loads(cache.read_text())) == n + 1   # n keys + _meta
    e.cx.cache.put_many(["one more"], ["s"], [{"s": 0.0}], "table")
    e.cx.save_cache()
    assert len(json.loads(cache.read_text())) == n + 1                     # below threshold: not written
    e.cx.flush_cache()
    assert len(json.loads(cache.read_text())) == n + 2


def test_load_context_flushes_cache_at_end(tmp_path):
    cache = tmp_path / "labels.json"
    e = SignalEvaluator(labeller=TableLabeller({}), label_cache_path=cache)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [happy_dossier()])
    data = json.loads(cache.read_text())
    assert data["_meta"] == {"format": CACHE_FORMAT, "model_id": "table"}
    assert len(data) > 1 and e.cx.labels_cover_corpus is True             # art_T1's one block, every sentence


def test_load_context_survives_unwritable_cache(monkeypatch, tmp_path):
    cache = tmp_path / "labels.json"
    monkeypatch.setattr(type(cache), "write_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    e = SignalEvaluator(labeller=TableLabeller({}), label_cache_path=cache)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [happy_dossier()])
    r = e.evaluate(happy_dossier())
    assert set(r) >= CONTRACT_KEYS and r["_facts"]["context_loaded"] is True


# ── 3. labeller "auto" mode (README l.225-226: self-contained, no network inside evaluate) ─────────
def test_default_is_auto_and_off_without_cache_or_local_model(no_local_model, monkeypatch):
    created = stub_classifier(monkeypatch)
    e = isolated(no_local_model)
    assert e.cx.classifier_mode == "auto" and e.cx.use_classifier is False
    assert e.cx.classifier_reason == "auto: no cache and no local model"
    r = e.evaluate(happy_dossier())
    assert r["_facts"]["classifier_reason"] == "auto: no cache and no local model"
    assert r["_facts"]["classifier_active"] is False
    assert created == []
    assert isolated(no_local_model, use_classifier=True).cx.classifier_mode == "auto"     # deprecated alias
    assert isolated(no_local_model, use_classifier=False).cx.classifier_reason == "disabled"


def test_auto_sets_hf_offline_before_any_model_code_runs(no_local_model):
    assert "HF_HUB_OFFLINE" not in os.environ
    isolated(no_local_model)
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_auto_respects_explicit_hf_offline_setting(no_local_model, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    isolated(no_local_model)
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_auto_enables_on_populated_label_cache(no_local_model, monkeypatch):
    created = stub_classifier(monkeypatch, available=False, error="offline")
    cache = no_local_model / "labels.json"
    cache.write_text(json.dumps({"_meta": {"format": CACHE_FORMAT, "model_id": MODEL_ID}, "somekey": 0.9}))
    e = SignalEvaluator(label_cache_path=cache)
    assert e.cx.use_classifier is True and e.cx.classifier_reason == "cache"
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [])
    assert created == [], "cache-only mode must not try to load a model at load_context"
    r = e.evaluate(happy_dossier())
    assert r["_facts"]["classifier_active"] is True and r["_facts"]["evidence"][0]["labels"]["unverifiable"] is True


def test_auto_ignores_empty_or_foreign_label_cache(no_local_model):
    cache = no_local_model / "labels.json"
    cache.write_text(json.dumps({"_meta": {"format": CACHE_FORMAT, "model_id": "other/model"}, "k": 0.5}))
    e = SignalEvaluator(label_cache_path=cache)
    assert e.cx.use_classifier is False and e.cx.classifier_reason == "auto: no cache and no local model"
    cache.write_text(json.dumps({"_meta": {"model": MODEL_ID, "format": "sentences-v2"}, "k": {"s": {}, "n": 1}}))
    assert SignalEvaluator(label_cache_path=cache).cx.use_classifier is False      # old per-text format


def test_auto_enables_on_local_model(no_local_model, monkeypatch):
    created = stub_classifier(monkeypatch)
    fake_local_model(no_local_model / "hub")
    e = isolated(no_local_model)
    assert e.cx.use_classifier is True and e.cx.classifier_reason == "local model"
    assert created == [], "construction must not instantiate the pipeline"
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [happy_dossier()])
    assert len(created) == 1
    r = e.evaluate(happy_dossier())
    assert len(created) == 1 and r["_facts"]["classifier_active"] is True
    assert r["_facts"]["classifier_reason"] == "local model"


def test_nli_mode_loads_model_at_load_context_not_evaluate(monkeypatch, tmp_path):
    created = stub_classifier(monkeypatch)
    e = isolated(tmp_path, labeller="nli")
    assert e.cx.classifier_reason == "enabled" and created == []
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [])
    assert len(created) == 1
    e.evaluate(happy_dossier())
    assert len(created) == 1


def test_disabled_reason(ev_nolabeller):
    r = ev_nolabeller.evaluate(happy_dossier())
    assert r["_facts"]["classifier_reason"] == "disabled" and r["_facts"]["classifier_active"] is False


def test_unavailable_reason_carries_the_error(monkeypatch, tmp_path):
    stub_classifier(monkeypatch, available=False, error="ImportError('no transformers')")
    e = isolated(tmp_path, labeller="nli")
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT], [happy_dossier()])
    r = e.evaluate(happy_dossier())
    assert r["_facts"]["classifier_active"] is False
    assert r["_facts"]["classifier_reason"] == "unavailable: ImportError('no transformers')"
    assert set(r) >= CONTRACT_KEYS


def test_context_init_does_not_import_transformers(no_local_model, monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", None)   # any `import transformers` now raises
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    fake_local_model(no_local_model / "hub")
    e = isolated(no_local_model)                               # auto -> "local model", still no import
    assert e.cx.classifier_reason == "local model"
    isolated(no_local_model, labeller="nli")


# ── never raise, never lose findings ──────────────────────────────────────────────────────────────
def test_unhashable_account_id_still_returns(ev):
    d = happy_dossier()
    d["account_id"] = ["acct_T"]
    r = ev.evaluate(d)
    assert set(r) >= CONTRACT_KEYS


def test_existing_facts_keys_preserved(ev):
    r = ev.evaluate(happy_dossier())
    assert set(r["_facts"]) >= {
        "context_loaded", "classifier_active", "labels_cover_corpus", "deserved_reason", "triggers", "trigger_source",
        "account_triggers_unattached", "reached_human", "days_to_renewal", "claim_status", "claim_detail", "cohort_notes",
        "security_review", "evidence", "verified_sources", "has_customer_text", "has_attached_text", "n_stale_evidence",
        "evidence_topics", "hypothesis_text_support", "duplicates", "context_loss", "final_state", "routed",
        "customer_visible", "errors", "classifier_reason",
    }
