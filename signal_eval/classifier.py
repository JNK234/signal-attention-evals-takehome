"""
ABOUTME: Optional zero-shot NLI labeller for the spec points that are about meaning in text.
ABOUTME: Scores hypothesis sentences on the head of a text (quoted/forwarded material is split off
ABOUTME: syntactically first); a per-sentence cache means a wording change rescores only that sentence.
"""

import hashlib

NLI_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
NLI_THRESHOLD = 0.5
CHUNK_CHARS = 1200          # ~300 tokens; model context is 512 tokens incl. hypothesis
CHUNK_OVERLAP = 200

# ── hypotheses ────────────────────────────────────────────────────────────────
# Several phrasings per label; the label score is the max. Recall must not depend on one wording.
# {account} / {names} are filled per artefact when known (instance-specific criteria).
# Every change here must pass analysis/recall_gate.py before the corpus is relabelled.
HYPOTHESES = {
    # spec §8.1 bullet 1
    "cancel_intent": [
        "The customer says they will not renew, are cancelling, or are leaving for another vendor.",
        "This is a notice that the customer does not intend to renew the contract.",
        "{account} is terminating or not renewing the subscription.",
    ],
    # spec §8.1 bullet 2 — v4: 'credit' / 'RCA' negotiations are not legal references
    "legal_reference": [
        "The customer's lawyers or legal counsel are involved, or a contractual breach or reserved rights are formally asserted.",
        "Legal counsel, lawyers, or a legal team are mentioned.",
        "A data-protection authority or other government regulator has been notified or involved.",
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
    # docs/domain.md corpus traps. quoted_history is detected structurally (util.split_quoted) — the NLI
    # cannot see formatting; every sentence below is scored on the HEAD of the text (the new message).
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
EXCLUSION_LABELS = ("sarcasm",)
TOPIC_LABELS = tuple(k for k in HYPOTHESES if k.startswith("topic:"))


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


def _names(account):
    return [n for n in ((account or {}).get("champion"), (account or {}).get("economic_buyer")) if n]


def text_key(text, account=None):
    """Cache key: the text plus the instance-specific names that shape some hypotheses."""
    return hashlib.sha1((text + "\x1f" + "|".join(_names(account))).encode()).hexdigest()


def filled_hypotheses(account):
    """label -> [sentence, ...] with {names}/{account} filled; name-dependent sentences dropped if unknown."""
    names = _names(account)
    acct = (account or {}).get("name") or "The customer"
    out = {}
    for label, hs in HYPOTHESES.items():
        out[label] = [h.format(names=" or ".join(names), account=acct) for h in hs if "{names}" not in h or names]
    return out


def assemble(sentence_scores, filled, threshold=NLI_THRESHOLD, mentions_other_account=False, n_chunks=None,
             quoted_history=False, quote_marker=None):
    """Build the label dict from per-sentence scores (scored on the head text). Missing sentences count as 0."""
    scores = {lab: round(max((sentence_scores.get(s, 0.0) for s in ss), default=0.0), 3) for lab, ss in filled.items()}
    out = {"unverifiable": False, "n_chunks": n_chunks, "scores": scores,
           "quoted_history": bool(quoted_history), "quote_marker": quote_marker}
    for lab in TRIGGER_LABELS + EXCLUSION_LABELS:
        out[lab] = scores[lab] >= threshold
    topics = {k[len("topic:"):]: scores[k] for k in TOPIC_LABELS}
    best = max(topics, key=topics.get)
    out["topic"] = best if topics[best] >= threshold else None
    # triggers found inside forwarded text about another account belong to that account, not this one
    out["triggers_belong_elsewhere"] = bool(mentions_other_account)
    return out


def is_stale(label, author_type=None, quote_in_tail=False):
    """Is the operative content not current evidence? Stale when the agent's quote was lifted from the
    quoted/forwarded tail of the artefact (docs/domain.md 'Quoted history': the new message is current,
    'the quoted text below it repeats an alarming complaint from months earlier') — a structural fact that
    needs no model — or when the head is a joke written by the customer (docs/domain.md 'Sarcasm': 'chat
    messages from friendly champions'). Internal notes with 'lol' are casual, not sarcastic evidence to
    discount. The label's own `quoted_history` flag is informational: an artefact with a quoted tail is
    still current evidence when the agent quoted its head."""
    if quote_in_tail:
        return True
    if not label or label.get("unverifiable"):
        return False
    return bool(label.get("sarcasm") and author_type in ("customer", None))


class TextClassifier:
    """Zero-shot NLI. score_sentences(text, sentences, head_only) → {sentence: P(entailment)}."""

    def __init__(self, model_name=NLI_MODEL, device=None):
        self.available = False
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

    def score_sentences(self, text, sentences):
        """Max over chunks per sentence. Raises on model error."""
        sentences = sorted(set(sentences))
        chunks = chunk_text(text)
        if not sentences or not chunks:
            return {}, len(chunks)
        per_chunk = []
        for ch in chunks:
            r = self.pipe(ch, candidate_labels=sentences, multi_label=True, hypothesis_template="{}")
            per_chunk.append(dict(zip(r["labels"], r["scores"])))
        return {s: round(max(c[s] for c in per_chunk), 4) for s in sentences}, len(chunks)
