"""
ABOUTME: SignalEvaluator — the public class the grader calls. Composes the Context (truth layer),
ABOUTME: the ordered rule checks (layer 1), and the scoring rubric (layer 2). Never raises on a bad dossier.
"""

import traceback

from . import scoring
from .checks import CHECKS
from .context import Context
from .util import ts

# The dossier fields every check iterates as a list of dicts (README l.218-222: a check must see well-typed input
# so one malformed entry cannot hide findings on the valid ones).
LIST_FIELDS = ("lifecycle", "actions", "evidence", "notifications", "hypotheses", "metrics_claimed")
# The rules that read artefact text through the labeller; named when it could not read anything (decision 7).
TEXT_RULES = ["P1", "Q2", "I5", "Q4"]
BOT_SOURCES = {"bot_alert", "billing_event"}     # cold path: the author is unknown, the source type says bot


def _unevaluated(ctx, cx):
    """Decision 7: when the labeller is unavailable, or every non-bot evidence artefact with text came back
    unreadable, one severity-0 meta entry says which rules were not evaluated over how many artefacts. Silence
    would read as a pass; the four keys and the score are untouched (scoring skips META_RULES)."""
    read = []
    for f in ctx.get("evidence_facts") or []:
        bot = f.get("author_type") == "bot" or (f.get("author_type") is None and f.get("type") in BOT_SOURCES)
        if bot or f.get("status") not in ("verified", "stale", "unverified"):
            continue
        lab = f.get("labels") or {}
        if lab.get("reason") in ("empty text", "empty quote"):
            continue
        read.append(not lab.get("unverifiable"))
    if not read or any(read):
        return None
    reason = cx.classifier_reason if cx.labeller is None else f"{cx.classifier_reason}; every current block unreadable"
    return {"step": -1, "rule": "UNEVALUATED", "severity": 0.0,
            "explanation": f"labeller unavailable ({reason}): P1 (text triggers), Q2/I5 (hypothesis fit), Q4 (sarcasm) "
                           f"not evaluated over {len(read)} artefact(s)"}


def _drop_malformed_entries(field, items, errors):
    """Keep the dict entries of a list field; record and drop everything else (README l.222: the valid
    entries still get evaluated instead of the whole check dying on the first bad one)."""
    kept = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            kept.append(item)
        else:
            errors.append({"check": "input", "error": f"{field}[{i}] is {type(item).__name__}, expected dict; dropped"})
    return kept


def _sanitize(dossier, errors):
    """Coerce the input to the shape the checks expect, recording every coercion in `errors`
    (README l.222: evaluate() "must still return something sensible"). One place, so no check has to
    defend itself against a string where a list should be. None means absent and is left alone."""
    if not isinstance(dossier, dict):
        errors.append({"check": "input", "error": f"dossier is {type(dossier).__name__}, expected dict; treated as {{}}"})
        return {}
    d = dict(dossier)
    md = d.get("metadata")
    if md is not None and not isinstance(md, dict):
        errors.append({"check": "input", "error": f"metadata is {type(md).__name__}, expected dict; treated as {{}}"})
        d["metadata"] = {}
    for field in LIST_FIELDS:
        v = d.get(field)
        if v is None:
            continue
        if not isinstance(v, list):
            errors.append({"check": "input", "error": f"{field} is {type(v).__name__}, expected list; treated as []"})
            d[field] = []
            continue
        d[field] = _drop_malformed_entries(field, v, errors)
    return d


class SignalEvaluator:
    """
    evaluate(dossier) -> {quality_score, risk_score, deserved_attention, violations}   (README §1, exactly)
    explain(dossier)  -> the same four keys plus "_facts": every extracted fact, for analysis and tests.

    load_context() is optional. Without it, corpus checks (I6, P4, M6, Q5) are skipped; the model
    still reads the quotes the dossier carries, so P1/Q2 keep working with lower confidence.
    Without a labeller, one UNEVALUATED meta entry (severity 0) in `violations` names the text rules
    that were not evaluated (decision 7) — silence is never a pass.
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
        """The grader's contract: exactly the four README keys."""
        return self._evaluate(dossier)[0]

    def explain(self, dossier: dict) -> dict:
        """The contract plus "_facts" — for analysis scripts, tests and saved runs, never for the grader."""
        result, facts = self._evaluate(dossier)
        return dict(result, _facts=facts)

    def _evaluate(self, dossier):
        """(result, facts): result is the four-key contract, facts every extracted fact."""
        errors = []
        d, cx = _sanitize(dossier, errors), self.cx
        ctx = {"errors": errors}
        try:
            ctx["acct"] = cx.account_facts(d)
        except Exception as exc:  # e.g. unhashable account_id — fall back to the empty snapshot
            ctx["acct"] = cx.account_facts({})
            errors.append({"check": "account_facts", "error": repr(exc)})
        violations = []
        for check, needs_context in CHECKS:
            if needs_context and not cx.loaded:
                continue
            try:
                violations += check(d, ctx, cx)
            except Exception as exc:  # a malformed field must never take the whole evaluation down
                errors.append({"check": check.__name__, "error": repr(exc),
                               "where": traceback.format_exc(limit=1).strip().splitlines()[-1]})
        try:
            meta = _unevaluated(ctx, cx)
            if meta:
                violations.append(meta)
                ctx["unevaluated"] = list(TEXT_RULES)
        except Exception as exc:
            errors.append({"check": "unevaluated", "error": repr(exc)})
        try:
            cx.save_cache()
        except Exception as exc:  # README l.225-226: a read-only host must not change the verdict
            errors.append({"check": "label_cache", "error": repr(exc)})

        try:
            deserved, why = scoring.deserved_attention(d, ctx, cx.loaded)
        except Exception as exc:
            deserved, why = False, f"scoring error: {exc!r}"
            errors.append({"check": "deserved_attention", "error": repr(exc)})
        try:
            risk = scoring.risk_score(d, ctx, violations, deserved)
        except Exception as exc:
            risk = 0.0
            errors.append({"check": "risk_score", "error": repr(exc)})
        try:
            quality = scoring.quality_score(violations)
        except Exception as exc:
            quality = 0.0
            errors.append({"check": "quality_score", "error": repr(exc)})
        try:
            facts = self._facts(ctx, why)
        except Exception as exc:  # README l.202-215: the contract keys and the findings ship regardless
            errors.append({"check": "facts", "error": repr(exc)})
            facts = self._facts({"errors": errors}, why)
        return {
            "quality_score": quality,
            "risk_score": risk,
            "deserved_attention": bool(deserved),
            "violations": violations,
        }, facts

    def _facts(self, ctx, deserved_reason):
        """Every extracted fact, so downstream analysis never has to re-derive it."""
        cx = self.cx
        return {
            "context_loaded": cx.loaded,
            "classifier_active": bool(cx.classifier_active),
            "classifier_reason": cx.classifier_reason,
            "labels_cover_corpus": bool(getattr(cx, "labels_cover_corpus", False)),
            "unevaluated": ctx.get("unevaluated", []),
            "deserved_reason": deserved_reason,
            "triggers": sorted(ctx.get("triggers", [])),
            "trigger_source": ctx.get("trigger_source"),
            "account_triggers_unattached": ctx.get("account_triggers_unattached", []),
            "reached_human": ctx.get("reached_human"),
            # risk_score prices a missed churn by tier; an unrecognised tier silently takes the smallest
            # scale, so the value we actually saw has to be visible in the record
            "acct_tier": (ctx.get("acct") or {}).get("tier"),
            "days_to_renewal": ctx.get("days_to_renewal"),
            "claim_status": ctx.get("claim_status", []),
            "claim_detail": ctx.get("claim_detail", []),
            "cohort_match": ctx.get("cohort_match"),
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
            "closed_at_after_terminal": ctx.get("closed_at_after_terminal"),
            "restricted_quoted_unrouted": ctx.get("restricted_quoted_unrouted", []),
            "routed": ctx.get("routed"),
            "customer_visible": ctx.get("visible"),
            "errors": ctx.get("errors", []),
        }
