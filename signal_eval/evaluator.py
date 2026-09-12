"""
ABOUTME: SignalEvaluator — the public class the grader calls. Composes the Context (truth layer),
ABOUTME: the ordered rule checks (layer 1), and the scoring rubric (layer 2). Never raises on a bad dossier.
"""

import traceback

from . import scoring
from .checks import CHECKS
from .context import Context
from .util import ts


class SignalEvaluator:
    """
    evaluate(dossier) -> {quality_score, risk_score, deserved_attention, violations, _facts}

    load_context() is optional. Without it, corpus checks (I6, P4, M6, Q5) are skipped; the model
    still reads the quotes the dossier carries, so P1/Q2 keep working with lower confidence.
    A check that throws on malformed input is recorded in _facts["errors"], not raised.
    """

    def __init__(self, **context_kwargs):
        self.cx = Context(**context_kwargs)

    # ── context ──────────────────────────────────────────────────────────────
    def load_context(self, accounts, owners, telemetry, artifacts, dossiers):
        self.cx.load(accounts, owners, telemetry, artifacts, dossiers)
        # closed_at index for the duplicate check ("two open signals")
        self.cx.closed_at = {d.get("signal_id"): ts(d.get("closed_at")) for d in dossiers or []}

    @property
    def loaded(self):
        return self.cx.loaded

    # ── evaluation ───────────────────────────────────────────────────────────
    def evaluate(self, dossier: dict) -> dict:
        d, cx = dossier or {}, self.cx
        ctx = {"acct": cx.account_facts(d), "errors": []}
        violations = []
        for check, needs_context in CHECKS:
            if needs_context and not cx.loaded:
                continue
            try:
                violations += check(d, ctx, cx)
            except Exception as exc:  # a malformed field must never take the whole evaluation down
                ctx["errors"].append({"check": check.__name__, "error": repr(exc),
                                      "where": traceback.format_exc(limit=1).strip().splitlines()[-1]})
        cx.save_cache()

        try:
            deserved, why = scoring.deserved_attention(d, ctx, cx.loaded)
        except Exception as exc:
            deserved, why = False, f"scoring error: {exc!r}"
            ctx["errors"].append({"check": "deserved_attention", "error": repr(exc)})
        try:
            risk = scoring.risk_score(d, ctx, violations, deserved)
        except Exception as exc:
            risk = 0.0
            ctx["errors"].append({"check": "risk_score", "error": repr(exc)})
        return {
            "quality_score": scoring.quality_score(violations),
            "risk_score": risk,
            "deserved_attention": bool(deserved),
            "violations": violations,
            "_facts": self._facts(ctx, why),
        }

    def _facts(self, ctx, deserved_reason):
        """Every extracted fact, so downstream analysis never has to re-derive it."""
        cx = self.cx
        return {
            "context_loaded": cx.loaded,
            "classifier_active": bool(cx.classifier_active),
            "labels_cover_corpus": bool(getattr(cx, "labels_cover_corpus", False)),
            "deserved_reason": deserved_reason,
            "triggers": sorted(ctx.get("triggers", [])),
            "trigger_source": ctx.get("trigger_source"),
            "account_triggers_unattached": ctx.get("account_triggers_unattached", []),
            "reached_human": ctx.get("reached_human"),
            "days_to_renewal": ctx.get("days_to_renewal"),
            "claim_status": ctx.get("claim_status", []),
            "claim_detail": ctx.get("claim_detail", []),
            "cohort_notes": ctx.get("cohort_notes", []),
            "security_review": ctx.get("security_review", False),
            "evidence": ctx.get("evidence_facts", []),
            "verified_sources": sorted(s for s in ctx["verified_sources"] if s) if ctx.get("verified_sources") is not None else None,
            "has_customer_text": ctx.get("has_customer_text"),
            "has_attached_text": ctx.get("has_attached_text"),
            "n_stale_evidence": len(ctx.get("stale", []) or []),
            "evidence_topics": ctx.get("evidence_topics", {}),
            "hypothesis_text_support": ctx.get("hypothesis_text_support"),
            "duplicates": ctx.get("duplicates", []),
            "context_loss": ctx.get("context_loss"),
            "final_state": ctx.get("final_state"),
            "routed": ctx.get("routed"),
            "customer_visible": ctx.get("visible"),
            "errors": ctx.get("errors", []),
        }
