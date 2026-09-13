"""
ABOUTME: Zero-shot NLI labeller: multilingual mDeBERTa scoring hypothesis sentences on whole blocks. The
ABOUTME: pipeline is built lazily (warm()/first label()), never at import; a batch failure marks its texts unreadable.
"""

import os
from pathlib import Path

from .base import LabelResult

MODEL_ID = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
BATCH_SIZE = 16


def _hub_cache_dir():
    """Where huggingface_hub keeps downloaded models, resolved the way the library does (env first)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def local_model_present(model_id=MODEL_ID):
    """Is the model already on disk? A plain directory scan of the hub cache — imports nothing from
    transformers/huggingface_hub and never touches the network (README l.225-226: self-contained)."""
    snapshots = _hub_cache_dir() / f"models--{model_id.replace('/', '--')}" / "snapshots"
    try:
        return snapshots.is_dir() and any(p.is_dir() and any(p.iterdir()) for p in snapshots.iterdir())
    except OSError:
        return False


class NLILabeller:
    """available: None until the pipeline is first built, then True/False; error: repr of the failure."""

    def __init__(self, model_id=MODEL_ID, device=None):
        self.model_id = model_id
        self.device = device
        self.pipe = None
        self.available = None
        self.error = None

    def warm(self):
        """Build the pipeline now (load_context / analysis time, never inside evaluate()). Returns available."""
        if self.available is None:
            try:
                from transformers import pipeline
                import torch
                device = self.device
                if device is None:
                    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else -1)
                self.pipe = pipeline("zero-shot-classification", model=self.model_id, device=device)
                self.available = True
            except Exception as exc:  # transformers/torch missing or model not downloadable
                self.pipe = None
                self.available = False
                self.error = repr(exc)
        return self.available

    def label(self, texts, label_sentences):
        sentences = sorted(set(label_sentences))
        scores, readable = [{} for _ in texts], [False] * len(texts)
        if not sentences or not self.warm():
            return LabelResult(scores, readable)
        live = [i for i, t in enumerate(texts) if t and t.strip()]
        for start in range(0, len(live), BATCH_SIZE):
            idx = live[start:start + BATCH_SIZE]
            try:
                out = self.pipe([texts[i] for i in idx], candidate_labels=sentences, multi_label=True,
                                hypothesis_template="{}")
            except Exception as exc:  # one bad batch must not take the rest down; its texts stay unreadable
                self.error = repr(exc)
                continue
            if isinstance(out, dict):
                out = [out]
            for i, r in zip(idx, out):
                scores[i] = {s: round(float(v), 4) for s, v in zip(r["labels"], r["scores"])}
                readable[i] = True
        return LabelResult(scores, readable)
