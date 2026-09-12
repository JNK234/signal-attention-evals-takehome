"""
ABOUTME: Optional zero-shot NLI labeller for the spec points that are about meaning in text.
ABOUTME: Works on any text length (chunked), any source (artefact or bare quote), and degrades to
ABOUTME: 'unverifiable' when the model is unavailable. Hypotheses come from spec §8.1 / §3.2 wording.
"""

import hashlib

NLI_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
NLI_THRESHOLD = 0.5
CHUNK_CHARS = 1200          # ~300 tokens; model context is 512 tokens incl. hypothesis
CHUNK_OVERLAP = 200

# ── hypotheses ────────────────────────────────────────────────────────────────
# Several phrasings per label; the label score is the max. Recall must not depend on one wording.
# {account} / {names} are filled per artefact when known (instance-specific criteria).
HYPOTHESES = {
    # spec §8.1 bullet 1
    "cancel_intent": [
        "The customer says they will not renew, are cancelling, or are leaving for another vendor.",
        "This is a notice that the customer does not intend to renew the contract.",
        "{account} is terminating or not renewing the subscription.",
    ],
    # spec §8.1 bullet 2
    "legal_reference": [
        "The text mentions legal counsel, lawyers, a contractual breach, or reserved rights.",
        "The customer references a regulator or regulatory body.",
    ],
    # spec §8.1 bullet 3
    "security_incident": [
        "The customer reports that a security breach, data leak, or unauthorized access actually occurred.",
        "Customer data was exposed, stolen, or accessed without authorization.",
    ],
    # spec §8.1 bullet 4
    "departure": [
        "A person is leaving the company or has resigned.",
        "{names} is leaving the company or has resigned.",
    ],
    # spec §8.1 bullet 5 (amount is read syntactically elsewhere)
    "billing_dispute": [
        "The invoice or payment is disputed, rejected, or overdue.",
    ],
    # docs/domain.md corpus traps — applied to the head of the text (the new message)
    "quoted_history": [
        "The new message is positive or resolved; any complaint appears only in quoted older text.",
        "The writer says the earlier issue below is closed and can be ignored.",
    ],
    "sarcasm": [
        "The message is a joke or sarcastic.",
    ],
    # spec §3.2 hypothesis classes — for Q2
    "topic:champion_departure": ["The internal advocate or budget holder is leaving or has left."],
    "topic:budget_pressure": ["The customer is under procurement, budget, or cost-cutting pressure."],
    "topic:product_gap": ["The customer needs a missing capability or names a competitor with it."],
    "topic:onboarding_failure": ["The customer never adopted the product; setup or rollout never happened."],
    "topic:reliability_erosion": ["Outages, latency, or errors have damaged the customer's trust."],
    "topic:benign_variation": ["The change in usage is planned, seasonal, a holiday, or otherwise expected."],
}
TRIGGER_LABELS = ("cancel_intent", "legal_reference", "security_incident", "departure", "billing_dispute")
EXCLUSION_LABELS = ("quoted_history", "sarcasm")
TOPIC_LABELS = tuple(k for k in HYPOTHESES if k.startswith("topic:"))
HEAD_ONLY = set(EXCLUSION_LABELS)   # scored on the first chunk only — they are about the *new* message

HYPOTHESES_HASH = hashlib.sha1(repr(sorted(HYPOTHESES.items())).encode()).hexdigest()[:10]


def chunk_text(text, size=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    text = text or ""
    if len(text) <= size:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return out


def text_key(text, account=None):
    """Cache key: the text plus the instance-specific names that shape the hypotheses."""
    names = _names(account)
    return hashlib.sha1((text + "\x1f" + "|".join(names) + "\x1f" + HYPOTHESES_HASH).encode()).hexdigest()


def _names(account):
    return [n for n in ((account or {}).get("champion"), (account or {}).get("economic_buyer")) if n]


class TextClassifier:
    """Zero-shot NLI labeller. label(text, account) → dict of booleans + scores, or unverifiable."""

    def __init__(self, model_name=NLI_MODEL, threshold=NLI_THRESHOLD, device=None):
        self.available = False
        self.threshold = threshold
        self.model_name = model_name
        self.error = None
        try:
            from transformers import pipeline
            import torch
            if device is None:
                device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else -1)
            self.pipe = pipeline("zero-shot-classification", model=model_name, device=device)
            self.available = True
        except Exception as exc:  # transformers/torch missing or model not downloadable
            self.pipe = None
            self.error = repr(exc)

    def _fill(self, label, account):
        names = _names(account)
        acct = (account or {}).get("name") or "The customer"
        out = []
        for h in HYPOTHESES[label]:
            if "{names}" in h and not names:
                continue
            out.append(h.format(names=" or ".join(names), account=acct))
        return out

    def label(self, text, account=None, mentions_other_account=False):
        if not self.available:
            return {"unverifiable": True, "reason": "classifier unavailable"}
        chunks = chunk_text(text)
        if not chunks:
            return {"unverifiable": True, "reason": "empty text"}
        filled = {lab: self._fill(lab, account) for lab in HYPOTHESES}
        sentences = sorted({s for ss in filled.values() for s in ss})
        try:
            per_chunk = []
            for ch in chunks:
                r = self.pipe(ch, candidate_labels=sentences, multi_label=True, hypothesis_template="{}")
                per_chunk.append(dict(zip(r["labels"], r["scores"])))
        except Exception as exc:
            return {"unverifiable": True, "reason": repr(exc)}
        scores = {}
        for lab, ss in filled.items():
            use = per_chunk[:1] if lab in HEAD_ONLY else per_chunk
            scores[lab] = round(max((c[s] for c in use for s in ss), default=0.0), 3)
        out = {"unverifiable": False, "n_chunks": len(chunks), "scores": scores}
        for lab in TRIGGER_LABELS + EXCLUSION_LABELS:
            out[lab] = scores[lab] >= self.threshold
        topics = {k[len("topic:"):]: scores[k] for k in TOPIC_LABELS}
        best = max(topics, key=topics.get)
        out["topic"] = best if topics[best] >= self.threshold else None
        # triggers found inside forwarded text about another account belong to that account, not this one
        out["triggers_belong_elsewhere"] = bool(mentions_other_account)
        return out


def is_stale(label, author_type=None):
    """Is the operative content not current evidence? Quoted history always; a joke only when the
    customer wrote it (docs/domain.md: 'chat messages from friendly champions'). Internal notes with
    'lol' are casual, not sarcastic evidence to discount."""
    if not label or label.get("unverifiable"):
        return False
    return bool(label.get("quoted_history") or (label.get("sarcasm") and author_type in ("customer", None)))
