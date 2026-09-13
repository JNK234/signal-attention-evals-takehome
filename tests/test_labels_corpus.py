"""
ABOUTME: Replays the recall-gate fixtures that point at corpus artefacts through the score cache alone (no model)
ABOUTME: and asserts the decided verdicts; skipped entirely when the cache for MODEL_ID is not on disk.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

from signal_eval.context import Context  # noqa: E402
from signal_eval.labellers import LabelCache, MODEL_ID, default_cache_path  # noqa: E402

_CACHE = default_cache_path(MODEL_ID)
if not (_CACHE.exists() and LabelCache(_CACHE).populated_for(MODEL_ID)):
    pytest.skip(f"no score cache for {MODEL_ID} at {_CACHE} (run analysis/label_all_artifacts.py)", allow_module_level=True)

import eval_takehome as E  # noqa: E402
import recall_gate  # noqa: E402

MAX_HELDOUT_ABSTAIN = 0.15   # calibrated held-out abstain rate is 11.6% (labels.THRESHOLDS comment); this pins the ceiling


def _corpus_rows():
    """Counted gate rows whose locator is a corpus artefact, read from the cache only. Calibration-half rows are
    in-sample (the thresholds were set on them); held-out rows are the real check. Both are asserted."""
    arts = E._load("artifacts.jsonl")
    cx = Context(labeller="cache")
    assert cx.labeller is not None, cx.classifier_reason
    cx.accounts = {a["account_id"]: a for a in E._load("accounts.jsonl")}
    rows = [r for r in recall_gate.load_cases(arts)
            if r["artifact"] is not None and r["artifact"]["artifact_id"] != "synthetic" and r["note"] is None]
    return recall_gate.score_cases(cx, rows)


ROWS = _corpus_rows()
_ID = [f"{r['split']}-{r['label']}-{r['id']}" for r in ROWS]


@pytest.mark.parametrize("row", ROWS, ids=_ID)
def test_corpus_fixture_verdict(row):
    """No confirmed error: the verdict equals the expectation or abstains. Rows whose id starts with 'calib-' are
    in-sample for the threshold table; 'heldout-' rows were never used to set it."""
    assert row["outcome"] != "missing"
    assert not (row.get("lab") or {}).get("unverifiable"), "corpus artefact not covered by the cache"
    assert row["got"] in (row["expected"], None), f"want {row['expected']!r}, got {row['got']!r} (score {row['score']})"


def test_heldout_abstain_rate_is_bounded():
    held = [r for r in ROWS if r["split"] == "heldout" and r["label"] not in recall_gate.STRUCTURAL]
    abst = [r for r in held if r["got"] is None]
    assert len(abst) / len(held) <= MAX_HELDOUT_ABSTAIN, [r["id"] for r in abst]


def test_heldout_has_both_classes_for_trigger_labels():
    """The split leaves every trigger label with at least one positive and one negative on the held-out half."""
    for label in ("cancel_intent", "legal_reference", "departure", "billing_dispute"):
        exp = {r["expected"] for r in ROWS if r["split"] == "heldout" and r["label"] == label}
        assert exp == {True, False}, (label, exp)
