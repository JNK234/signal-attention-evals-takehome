"""
ABOUTME: Spec-conformance evaluator for account-risk signal dossiers (Signal Labs take-home).
ABOUTME: Public surface: SignalEvaluator, the RULES table, and the Context loader.
"""

from .evaluator import SignalEvaluator
from .labellers import MODEL_ID, TableLabeller
from .spec import RULES, RULE_REF, RULE_SEVERITY, SEV_WEIGHT

__all__ = ["SignalEvaluator", "RULES", "RULE_REF", "RULE_SEVERITY", "SEV_WEIGHT", "TableLabeller", "MODEL_ID"]
