"""
Signal Labs Attention-Eval Take-Home
====================================

Implement the SignalEvaluator class below.

Run your evaluator locally:
    python eval_takehome.py

See README.md for full instructions, spec.pdf for the formal specification, and
docs/domain.md for the glossary and the list of known telemetry data events.
"""

import json
from pathlib import Path

DATA = Path("data")


class SignalEvaluator:
    """
    Evaluate account-risk signal dossiers against the attention-agent specification.

    Your evaluator should assess:
    - Spec compliance (does the dossier follow the signal lifecycle and the policy rules?)
    - Evidence integrity (are the quotes and the numbers real?)
    - Whether the signal deserved a human's attention at all
    - Risk (how likely is this dossier to cause harm -- a complaint, a wasted
      escalation, or a missed churn?)

    You may use any tools, models or libraries you want.
    """

    def __init__(self):
        """
        Initialize your evaluator.
        Load any models, rules, resources or cached data you need.
        Must work without arguments.
        """
        pass

    def load_context(self, accounts, owners, telemetry, artifacts, dossiers):
        """
        OPTIONAL. Called once, before any call to evaluate().

        Several spec rules cannot be checked from a dossier alone -- evidence
        integrity (I6), quantitative grounding (M6) and cross-tenant isolation
        need the rest of the corpus. This is how you get it.

        Args:
            accounts:  list of dicts from data/accounts.jsonl
            owners:    list of dicts from data/owners.jsonl
            telemetry: list of dicts from data/telemetry.jsonl
            artifacts: list of dicts from data/artifacts.jsonl
            dossiers:  list of dicts from data/signal_dossiers.jsonl -- the full
                       set you are about to be asked about. Some rules (duplicate
                       signals, cohort-level checks) need this.

        If you implement this, we will call it before evaluating. evaluate()
        must still return something sensible if it was never called.
        """
        pass

    def evaluate(self, dossier: dict) -> dict:
        """
        Evaluate a single signal dossier.

        Args:
            dossier: dict with keys:
                - signal_id: str
                - account_id: str
                - detector: str
                - opened_at, closed_at: ISO-8601 timestamps
                - lifecycle:       list of {step, from_state, to_state, at, trigger, reason}
                - hypotheses:      list of {step, hypothesis, confidence, evidence_refs, rationale}
                - evidence:        list of {step, artifact_id, source, restricted, attached_at, quote}
                - metrics_claimed: list of {step, metric, window_days, as_of,
                                            value_before, value_after, claim}
                - actions:         list of {step, action, at, params}
                - notifications:   list of {step, at, channel, locale, owner_id, attempt}
                - scoring:  {severity, confidence, arr_at_risk, scored_at}
                - decision: {disposition, recommended_play, customer_visible, reason}
                - metadata: {account_tier, arr_annual, materiality_floor, owner_timezone,
                             owner_locale, owner_preferred_channel, account_flags,
                             days_to_renewal, ...}

        Returns:
            dict with keys:
                - quality_score: float 0-1        (higher = better dossier)
                - risk_score: float 0-1           (higher = more likely to cause harm)
                - deserved_attention: bool        (should this have reached a human?)
                - violations: list of {
                    step: int,
                    rule: str,          # reference to the spec rule violated
                    severity: float,    # 0-1
                    explanation: str
                  }
        """
        # TODO: Implement your evaluation logic here
        return {
            "quality_score": 0.5,
            "risk_score": 0.5,
            "deserved_attention": False,
            "violations": [],
        }


def _load(name):
    path = DATA / name
    if not path.exists():
        return None
    with open(path) as f:
        return [json.loads(line) for line in f]


def main():
    """Run evaluator on sample data for local testing."""
    dossiers = _load("signal_dossiers.jsonl")
    if dossiers is None:
        print("No data found. Make sure data/signal_dossiers.jsonl exists.")
        return

    evaluator = SignalEvaluator()

    accounts = _load("accounts.jsonl")
    owners = _load("owners.jsonl")
    artifacts = _load("artifacts.jsonl")
    telemetry = _load("telemetry.jsonl")
    if hasattr(evaluator, "load_context"):
        evaluator.load_context(accounts, owners, telemetry, artifacts, dossiers)

    print(f"Evaluating {len(dossiers)} dossiers...")

    results = []
    for d in dossiers[:10]:
        r = evaluator.evaluate(d)
        results.append(r)
        print(
            f"  {d['signal_id']}: quality={r['quality_score']:.2f}, "
            f"risk={r['risk_score']:.2f}, deserved={r['deserved_attention']}, "
            f"violations={len(r['violations'])}"
        )

    print(f"\nEvaluated {len(results)} dossiers.")


if __name__ == "__main__":
    main()
