"""
ABOUTME: The label set: hypothesis sentences per label (written from the spec bullets), the per-model
ABOUTME: threshold table with an abstain band, and decide() — scoring (labellers/) is kept apart from deciding.
"""

import hashlib
import re

LABEL_SET_VERSION = "v5-blocks"

# ── hypotheses ────────────────────────────────────────────────────────────────
# Several phrasings per label; the label score is the max. Recall must not depend on one wording.
# {account} / {names} / {role} are filled per artefact when known (instance-specific criteria); a sentence
# whose placeholder is unknown is dropped. Sentences are written from the spec bullet wording and are never
# edited to flip one example (plan, anti-fitting rule 6).
LABEL_HYPOTHESES = {
    # spec §8.1 bullet 1
    "cancel_intent": [
        "The customer says they will not renew, are cancelling, or are leaving for another vendor.",
        "This is a notice that the customer does not intend to renew the contract.",
        "{account} is terminating or not renewing the subscription.",
        "An explicit written statement of intent to cancel or not renew, from a customer-side author.",   # spec line, verbatim (§8.1 bullet 1)
    ],
    # spec §8.1 bullet 2 — assertions, not mentions: 'credit' / 'RCA' negotiations are not legal references
    "legal_reference": [
        "The customer has engaged legal counsel or lawyers.",
        "The customer asserts a contractual breach or reserves its legal rights.",
        "A regulator or data-protection authority has been notified or is involved.",
        "A reference to legal counsel, contractual breach, reserved rights or a regulatory body.",   # spec line, verbatim (§8.1 bullet 2)
    ],
    # spec §8.1 bullet 3 — the bullet ("A security or data-privacy incident raised by the customer") is a topic phrase: as a
    # hypothesis it scores 0.90–0.99 on an SSO feature request, a row-level-security ask and a routine pen-test note
    # (gate, 2026-09-13), so the assertion sentences stand in for it
    "security_incident": [
        "The customer reports that a security breach, data leak, or unauthorized access actually occurred.",
        "Customer data was exposed, stolen, or accessed without authorization.",
    ],
    # spec §8.1 bullet 4 — the person, the named champion/buyer, or their role
    "departure": [
        "A person is leaving the company or has resigned.",
        "{names} has left or is leaving the company.",
        "The {role} has left or is leaving the company.",
        "Departure of the economic buyer or named champion.",   # spec line, verbatim (§8.1 bullet 4; fixes the 'confirmed last day is EOM' notes, also fires on 'champion moved to a new team … no risk change' — unattributed downstream, see analysis/recall_gate.py)
    ],
    # spec §8.1 bullet 5 (amount is read syntactically elsewhere) — the bullet ("A billing dispute exceeding 5% of the
    # account's annual ARR") scores 0.98 on a PAID invoice and 0.89 on an approved credit memo (gate, 2026-09-13), so the
    # assertion sentence stands in for it
    "billing_dispute": [
        "The invoice or payment is disputed, rejected, or overdue.",
    ],
    # docs/domain.md corpus traps. Quoted history is a structural fact (text.blocks depth), not a label.
    "sarcasm": [
        "The message is a joke or sarcastic.",
    ],
    # spec §3.2 hypothesis classes — for Q2
    "topic:champion_departure": [
        "The internal advocate or budget holder is leaving or has left.",
        "The internal advocate or economic buyer is leaving or has left.",   # spec line, verbatim (§3.2)
    ],
    "topic:budget_pressure": [
        "The customer is under procurement, budget, or cost-cutting pressure.",
        "Procurement or cost-cutting pressure, not a product problem.",   # spec line, verbatim (§3.2)
    ],
    # §3.2 "A missing capability, often with a competitor named" — the one-sentence form scored 0.006 on a clear
    # positive under the chosen model (analysis/model_bakeoff.py); each clause of the definition is its own sentence
    "topic:product_gap": [
        "The customer cannot do something they need because the product lacks the feature.",
        "The customer names a competitor whose product has a feature this one lacks.",
        "The product is missing a capability the customer needs.",
        "A missing capability, often with a competitor named.",   # spec line, verbatim (§3.2)
    ],
    # §3.2 "The account never reached value; adoption never started"
    "topic:onboarding_failure": [
        "The customer has not started using the product; setup or rollout never happened.",
        "Months after purchase the workspace is still not set up or adopted.",
        "The account never reached value from the product; adoption never started.",
        "The account never reached value; adoption never started.",   # spec line, verbatim (§3.2)
    ],
    "topic:reliability_erosion": [
        "Outages, latency, or errors have damaged the customer's trust.",
        "Outages or latency have damaged trust.",   # spec line, verbatim (§3.2)
    ],
    # §3.2 "The anomaly is explained by seasonality, a data event, or a planned change"
    "topic:benign_variation": [
        "The drop in usage is expected: a holiday, a seasonal period, or a planned change.",
        "Usage is lower for a known, planned reason and is not a concern.",
        "The change in usage is explained by seasonality, a data event, or a planned change.",
        "The anomaly is explained by seasonality, a data event, or a planned change.",   # spec line, verbatim (§3.2)
    ],
}
TRIGGER_LABELS = ("cancel_intent", "legal_reference", "security_incident", "departure", "billing_dispute")
EXCLUSION_LABELS = ("sarcasm",)
TOPIC_LABELS = tuple(k for k in LABEL_HYPOTHESES if k.startswith("topic:"))
BOT_LABELS = ("billing_dispute",)     # a bot artefact is a system record: only the structural billing read

# ── thresholds ────────────────────────────────────────────────────────────────
# (low, high) per label per model: score ≤ low → False, ≥ high → True, between → None (abstain).
# Default band for a label analysis/calibrate_thresholds.py could not calibrate (fewer than 2 positives or 2
# negatives on the calibration half) and for any model without a table.
DEFAULT_BAND = (0.35, 0.65)
# Set by analysis/calibrate_thresholds.py on the calibration half of the recall-gate fixtures (split by
# sha1(label | current text) % 2) and pasted here after review. DEFAULT_BAND is the data-free prior; the calibrator
# may only WIDEN the abstain region (low below 0.35, high above 0.65) — never let the evaluator claim more than the
# prior would ("cautious-only") — and never puts the low edge under the 0.05 noise floor. Held-out confusion under
# this table with the spec-verbatim sentences included (expected × got; P? / N? = abstain on a positive / negative):
#   label                        n  TP  FN  P?  TN  FP  N?   errors
#   cancel_intent               18   6   0   0  12   0   0
#   legal_reference              8   6   0   0   2   0   0
#   security_incident            3   1   0   0   2   0   0
#   departure                   10   6   0   0   3   1   0   art_00100 "champion moved to a new team internally … no risk
#                                                            change" reads as a departure (names nobody → unattributed downstream)
#   billing_dispute              4   2   0   0   2   0   0
#   sarcasm                      8   8   0   0   0   0   0
#   topic:champion_departure     1   1   0   0   0   0   0
#   topic:budget_pressure        2   1   0   0   1   0   0
#   topic:product_gap            5   1   0   1   3   0   0
#   topic:onboarding_failure     4   1   0   1   2   0   0
#   topic:reliability_erosion    2   1   0   0   1   0   0
#   topic:benign_variation       3   1   1   0   1   0   0   art_01881
#   total                       68  35   1   2  29   1   0   → 64/68 correct, 2 errors, 2 abstain (2.9%)
THRESHOLDS = {
    "MoritzLaurer/deberta-v3-base-zeroshot-v2.0": {
        "cancel_intent": (0.350, 0.650),   # prior (calibration would narrow it; cautious-only forbids that)
        "legal_reference": (0.350, 0.650),   # uncalibrated: n too small
        "security_incident": (0.350, 0.650),   # uncalibrated: n too small
        "departure": (0.206, 0.650),   # low edge widened: calib positives down to 0.497 (spec line lifted the EOM notes)
        "billing_dispute": (0.350, 0.650),   # uncalibrated: n too small
        "sarcasm": (0.121, 0.922),   # classes overlap: 'jk' chat at 0.13, internal 'lol' template at 0.91
        "topic:champion_departure": (0.350, 0.650),   # uncalibrated: n too small
        "topic:budget_pressure": (0.350, 0.650),   # uncalibrated: n too small
        "topic:product_gap": (0.350, 0.650),   # uncalibrated: n too small
        "topic:onboarding_failure": (0.350, 0.650),   # uncalibrated: n too small
        "topic:reliability_erosion": (0.350, 0.650),   # uncalibrated: n too small
        "topic:benign_variation": (0.350, 0.650),   # prior (one calib positive at the noise floor is a model miss, not an edge)
    },
}

# Block-context pins (plan, decision 1): (block text, label, expected verdict). Deterministic tests replay them
# through the TableLabeller; WP-D's calibration must hold them with the real model.
KNOWN_CASES = [
    ("We considered cancelling. We decided not to.", "cancel_intent", False),
    ("We love the product. That said, we will not be renewing.", "cancel_intent", True),
]


def decide(score, label, model_id=None):
    """True / False / None (abstain) from a score under the model's band for the label."""
    if score is None:
        return None
    low, high = THRESHOLDS.get(model_id, {}).get(label, DEFAULT_BAND)
    if score >= high:
        return True
    if score <= low:
        return False
    return None


# ── filling ───────────────────────────────────────────────────────────────────
def _names(account):
    return [n for n in ((account or {}).get("champion"), (account or {}).get("economic_buyer")) if n]


def _roles(account):
    return [r for r in ((account or {}).get("champion_title"), (account or {}).get("economic_buyer_title")) if r]


def filled_hypotheses(account, labels=None):
    """label -> [sentence, ...] with {names}/{role}/{account} filled; placeholder sentences dropped if unknown."""
    names, roles = _names(account), _roles(account)
    acct = (account or {}).get("name") or "The customer"
    out = {}
    for label, hs in LABEL_HYPOTHESES.items():
        if labels is not None and label not in labels:
            continue
        sentences = []
        for h in hs:
            if "{role}" in h:
                sentences += [h.format(role=r) for r in roles]
            elif "{names}" in h:
                if names:
                    sentences.append(h.format(names=" or ".join(names)))
            else:
                sentences.append(h.format(account=acct))
        out[label] = sentences
    return out


def _template_re(template):
    return re.compile("^" + re.sub(r"\\\{(?:names|role|account)\\\}", ".+?", re.escape(template)) + "$")


_TEMPLATES = [(label, _template_re(h)) for label, hs in LABEL_HYPOTHESES.items() for h in hs]


def label_of(sentence):
    """The label a filled hypothesis sentence belongs to, or None."""
    for label, rx in _TEMPLATES:
        if rx.match(sentence):
            return label
    return None


def text_key(text, sentence, model_id):
    """Cache key: one score per (block text, hypothesis sentence, model)."""
    return hashlib.sha1(f"{text}\x1f{sentence}\x1f{model_id}".encode()).hexdigest()
