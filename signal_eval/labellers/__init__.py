"""
ABOUTME: Labeller engines behind one contract (base.Labeller) and resolve(): which engine a Context uses —
ABOUTME: an explicit instance, the NLI model when local, the cache alone when populated, or nothing, with the reason.
"""

import os

from .base import LabelResult, Labeller, TableLabeller
from .cache import CacheOnlyLabeller, CachedLabeller, LabelCache, default_cache_path
from .nli import MODEL_ID, NLILabeller, local_model_present

__all__ = ["LabelResult", "Labeller", "TableLabeller", "CacheOnlyLabeller", "CachedLabeller", "LabelCache",
           "NLILabeller", "MODEL_ID", "local_model_present", "default_cache_path", "resolve"]


def resolve(labeller="auto", cache_path=None):
    """(labeller | None, reason). README l.225-226: the evaluator is self-contained and makes no external calls
    inside evaluate(). "auto" therefore only turns the model on when it cannot possibly need the network: the
    model files already on disk, or a populated cache for MODEL_ID. "nli" forces the model (analysis time; may
    download). Nothing here imports transformers; that stays lazy inside NLILabeller. Every engine is wrapped
    with the cache (in memory when cache_path is None)."""
    if labeller is None or labeller is False:
        return None, "disabled"
    if labeller == "auto":
        # setdefault: an explicit user setting wins. Must happen before transformers/huggingface_hub is first
        # imported, which is why it lives here and not next to the pipeline call.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        cache = LabelCache(cache_path if cache_path is not None else default_cache_path(MODEL_ID))
        if local_model_present(MODEL_ID):
            return CachedLabeller(NLILabeller(MODEL_ID), cache), "local model"
        if cache.populated_for(MODEL_ID):
            return CacheOnlyLabeller(cache, MODEL_ID), "cache"
        return None, "auto: no cache and no local model"
    if labeller == "cache":
        cache = LabelCache(cache_path if cache_path is not None else default_cache_path(MODEL_ID))
        if cache.populated_for(MODEL_ID):
            return CacheOnlyLabeller(cache, MODEL_ID), "cache"
        return None, "cache: no populated cache"
    if labeller == "nli" or labeller is True:
        cache = LabelCache(cache_path if cache_path is not None else default_cache_path(MODEL_ID))
        return CachedLabeller(NLILabeller(MODEL_ID), cache), "enabled"
    if hasattr(labeller, "label"):
        return CachedLabeller(labeller, LabelCache(cache_path)), "explicit"
    raise ValueError(f"labeller must be 'auto', 'cache', 'nli', None or a Labeller, got {labeller!r}")
