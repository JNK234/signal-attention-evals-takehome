"""
ABOUTME: Score cache keyed on (block text, hypothesis sentence, model id) so engines can be compared on one
ABOUTME: corpus; batched writes. CachedLabeller wraps any engine; CacheOnlyLabeller serves hits and abstains on misses.
"""

import json
from pathlib import Path

from ..labels import text_key
from .base import LabelResult

CACHE_FORMAT = "block-label-v3"
CACHE_FLUSH_EVERY = 1000     # new keys pending before save() actually writes (~50 blocks × ~20 sentences)


def default_cache_path(model_id, root=None):
    """analysis/.cache/labels_<model-slug>.json next to the package unless a root is given."""
    root = Path(root) if root else Path(__file__).resolve().parents[2] / "analysis" / ".cache"
    return root / f"labels_{model_id.replace('/', '--')}.json"


class LabelCache:
    """{"_meta": {"format", "model_id"}, key: score}. path=None keeps everything in memory."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.model_id = None
        self._scores = {}
        self._dirty = 0
        self._load()

    def _load(self):
        if not (self.path and self.path.exists()):
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            return
        meta = data.get("_meta") or {}
        if meta.get("format") != CACHE_FORMAT:
            return
        self.model_id = meta.get("model_id")
        self._scores = {k: v for k, v in data.items() if k != "_meta"}

    def __len__(self):
        return len(self._scores)

    def populated_for(self, model_id):
        return bool(self._scores) and self.model_id == model_id

    def get_many(self, texts, sentences, model_id):
        """Per text: {sentence: score} for the sentences that are cached (partial dicts on misses)."""
        out = []
        for t in texts:
            hit = {}
            for s in sentences:
                v = self._scores.get(text_key(t, s, model_id))
                if v is not None:
                    hit[s] = v
            out.append(hit)
        return out

    def put_many(self, texts, sentences, scores, model_id):
        if self.model_id is None:
            self.model_id = model_id
        for t, sc in zip(texts, scores):
            for s in sentences:
                if s in sc:
                    k = text_key(t, s, model_id)
                    if k not in self._scores:
                        self._dirty += 1
                    self._scores[k] = sc[s]

    def save(self):
        """Write only once CACHE_FLUSH_EVERY new keys are pending — persistence is a side effect and must not
        cost a file write per dossier. Raises on I/O failure; evaluate() records that instead of surfacing it
        (README l.225-226: the result must not depend on the host filesystem)."""
        if self._dirty >= CACHE_FLUSH_EVERY:
            self.flush()

    def flush(self):
        """Force-write pending scores now (end of load_context, end of a CLI run). Raises on failure."""
        if self.path and self._dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(self._scores)
            payload["_meta"] = {"format": CACHE_FORMAT, "model_id": self.model_id}
            self.path.write_text(json.dumps(payload))
            self._dirty = 0


class CachedLabeller:
    """Serve cached scores; send only the texts with a missing sentence to the engine; cache what comes back."""

    def __init__(self, inner, cache):
        self.inner = inner
        self.cache = cache

    @property
    def model_id(self):
        return self.inner.model_id

    @property
    def available(self):
        return getattr(self.inner, "available", True)

    @property
    def error(self):
        return getattr(self.inner, "error", None)

    def warm(self):
        return self.inner.warm() if hasattr(self.inner, "warm") else True

    def label(self, texts, label_sentences):
        sentences = sorted(set(label_sentences))
        hits = self.cache.get_many(texts, sentences, self.model_id)
        readable = [len(h) == len(sentences) for h in hits]
        missing = [i for i, ok in enumerate(readable) if not ok]
        if missing:
            res = self.inner.label([texts[i] for i in missing], sentences)
            self.cache.put_many([texts[i] for i in missing], sentences, res.scores, self.model_id)
            for i, sc, ok in zip(missing, res.scores, res.readable):
                hits[i].update(sc)
                readable[i] = bool(ok)
        return LabelResult(hits, readable)


class CacheOnlyLabeller:
    """No engine: a text with every sentence cached is readable, anything else is not."""

    available = True
    error = None

    def __init__(self, cache, model_id):
        self.cache = cache
        self.model_id = model_id

    def label(self, texts, label_sentences):
        sentences = sorted(set(label_sentences))
        hits = self.cache.get_many(texts, sentences, self.model_id)
        return LabelResult(hits, [len(h) == len(sentences) for h in hits])
