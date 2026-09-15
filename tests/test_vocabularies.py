"""
ABOUTME: The unknown-value contract: a field carrying a value outside the corpus vocabulary must be treated
ABOUTME: as unknown, never as a confident negative, and the closed vocabularies must have one definition each.
"""

import pytest
from conftest import ACCOUNT, ARTIFACT, OWNER, SignalEvaluator, explain, happy_dossier, rules, with_labels
from signal_eval.labellers import TableLabeller

CANCEL = {"backfill": {"cancel_intent": 0.9}}


def _eval(artifacts=None, accounts=None, labels=None):
    e = SignalEvaluator(labeller=TableLabeller(labels or {}))
    e.load_context(accounts or [ACCOUNT], [OWNER], [], artifacts or [ARTIFACT], [])
    return e


# ── author_type: a value we do not recognise is not evidence of "not a customer" ──

@pytest.mark.parametrize("author_type", ["Customer", "CUSTOMER", "customer "])
def test_a_differently_cased_customer_is_still_a_customer(author_type):
    """The corpus writes "customer"; another export may write "Customer". Reading that as not-a-customer
    silently discards the strongest evidence deserved_attention has."""
    art = dict(ARTIFACT, author_type=author_type)          # quote stays verbatim; only the casing differs
    r = explain(_eval([art]), happy_dossier())
    assert r["_facts"]["has_customer_text"] is True, r["_facts"]["evidence"]


def test_an_unrecognised_author_type_is_unknown_not_confidently_internal():
    """"end_user" is not in {customer, internal, bot}. The safe reading is "we do not know who wrote this",
    which is how a MISSING author_type is already handled — an unrecognised one must not be treated as
    more certain than a missing one."""
    art = dict(ARTIFACT, author_type="end_user", text="We are cancelling our contract.")
    known_unknown = dict(ARTIFACT, author_type=None, text="We are cancelling our contract.")
    labels = {"cancelling": {"cancel_intent": 0.9}}
    d = happy_dossier()
    unrecognised = explain(_eval([art], labels=labels), d)["_facts"]["trigger_source"]
    missing = explain(_eval([known_unknown], labels=labels), d)["_facts"]["trigger_source"]
    assert unrecognised == missing, f"unrecognised {unrecognised} != missing {missing}"


# ── severity: an unknown value must not silently switch a check off ───────────

def test_an_unknown_severity_does_not_silently_skip_the_time_to_attention_check():
    """T3 (spec §6.3) compares the first notification against a per-severity target. An unrecognised
    severity has no target, and skipping the check without saying so reads as a pass. mandatory.py already
    falls back to the P1 target for exactly this case; timing must not disagree with it."""
    d = happy_dossier()
    d["scoring"]["severity"] = "SEV1"                     # not one of P0..P3
    d["actions"][2]["params"]["severity"] = "SEV1"
    d["notifications"][0]["at"] = "2026-03-20T10:00:00Z"  # ~19 days after open: late under any target
    for e in d["lifecycle"]:
        if e.get("to_state") in ("routed", "acknowledged"):
            e["at"] = "2026-03-20T10:00:00Z"
    r = explain(_eval(), d)
    assert "T3" in rules(r) or r["_facts"].get("unevaluated"), \
        "an unknown severity turned off T3 with no violation and no UNEVALUATED marker"


def test_an_unknown_tier_is_recorded_not_just_silently_defaulted():
    """risk_score scales by tier. Falling back to the smallest account is a defensible default, but the
    fallback must be visible or a whole tier can be mispriced without anyone noticing."""
    acct = dict(ACCOUNT, tier="strategic")
    r = explain(_eval(accounts=[acct]), happy_dossier())
    assert r["_facts"]["acct_tier"] == "strategic"


# ── one definition per vocabulary ─────────────────────────────────────────────

def test_the_hypothesis_classes_have_a_single_definition():
    """The seven class names live in spec.py, in the labeller's topic: keys and in the lifecycle check.
    Three copies means renaming one silently changes what the others believe."""
    from signal_eval.labels import TOPIC_LABELS
    from signal_eval.spec import HYPOTHESIS_CLASSES

    from signal_eval.checks.lifecycle import HYPOTHESIS_CLASSES as lifecycle_classes
    assert lifecycle_classes is HYPOTHESIS_CLASSES or set(lifecycle_classes) == set(HYPOTHESIS_CLASSES)
    topics = {t[len("topic:"):] for t in TOPIC_LABELS}
    assert topics <= set(HYPOTHESIS_CLASSES), f"labeller topics not in the spec vocabulary: {topics - set(HYPOTHESIS_CLASSES)}"


def test_benign_variation_is_named_from_the_vocabulary_not_a_literal():
    """Q2 treats exactly one hypothesis class specially: benign_variation needs a cohort move or seasonal
    text to be supported. If that name is a bare string in the check it can drift from the labeller's key,
    and the special case silently stops firing."""
    import inspect

    from signal_eval.checks import quality
    from signal_eval.spec import BENIGN_CLASS
    assert quality.BENIGN_CLASS == BENIGN_CLASS, "quality.py must import the constant, not redefine it"
    src = inspect.getsource(quality)
    assert f'"{BENIGN_CLASS}"' not in src and f"'{BENIGN_CLASS}'" not in src, \
        "the class name is hardcoded as a literal somewhere in quality.py"


# ── the closed-world behaviour that already works, pinned so it cannot regress ──

def test_an_unknown_state_still_raises_a_transition_violation():
    d = happy_dossier()
    d["lifecycle"][3]["to_state"] = "triage"
    d["lifecycle"][4]["from_state"] = "triage"
    assert "TM" in rules(_eval().evaluate(d))


def test_an_unknown_action_still_raises_a_violation():
    d = happy_dossier()
    d["actions"][1]["action"] = "escalate_to_legal"
    assert "I4" in rules(_eval().evaluate(d))


def test_an_unknown_hypothesis_class_still_raises_a_violation():
    d = happy_dossier()
    d["hypotheses"][0]["hypothesis"] = "vendor_consolidation"
    assert "I5" in rules(_eval().evaluate(d))


def test_an_unknown_confidence_level_still_raises_a_violation():
    """spec §3.2: "Each hypothesis also carries a confidence level: high, medium or low." Unlike the three
    vocabularies above, an unrecognised confidence is not merely unreported — it silently weakens two other
    rules, because P5 reads `"high" in confs` (a misspelled "high" escapes the two-source test) and I1 reads
    `conf != "low"` (an unknown value reads as a backward-edge violation)."""
    d = happy_dossier()
    d["hypotheses"][0]["confidence"] = "very_high"
    assert "I5" in rules(_eval().evaluate(d))


def test_an_absent_confidence_level_still_raises_a_violation():
    d = happy_dossier()
    d["hypotheses"][0].pop("confidence")
    assert "I5" in rules(_eval().evaluate(d))


def test_an_unknown_detector_still_raises_a_violation():
    """spec §3.1: "A signal opens when exactly one of eight detectors fires" — a name outside the eight is
    not one of them, and the signal has no legitimate opening."""
    d = happy_dossier()
    d["detector"] = "vibes_detector"
    d["lifecycle"][0]["trigger"] = "detector:vibes_detector"
    assert "I5" in rules(_eval().evaluate(d)) or "TM" in rules(_eval().evaluate(d))


def test_the_detector_vocabulary_has_one_definition():
    """The eight names live in spec.py; a second copy would let the two drift."""
    import inspect
    from signal_eval import spec
    from signal_eval.checks import lifecycle
    assert len(spec.DETECTORS) == 8, "spec §3.1 lists exactly eight detectors"
    src = inspect.getsource(lifecycle)
    for name in spec.DETECTORS:
        assert f'"{name}"' not in src, f"{name} is hardcoded as a literal in lifecycle.py"
