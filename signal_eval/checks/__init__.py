"""
ABOUTME: The ordered list of rule checks evaluate() runs. Each check is a pure function
ABOUTME: (dossier, ctx, cx) -> [violation]; ctx is per-dossier scratch, cx is the Context (loaded or cold).
"""

from .duplicates import check_duplicates
from .evidence import check_evidence
from .grounding import check_grounding
from .lifecycle import check_actions, check_hypothesis_count, check_transitions
from .mandatory import check_mandatory_route
from .materiality import check_materiality
from .policy import check_policy
from .quality import check_quality
from .timing import check_timing

# (check, needs_context). Order matters: later checks read ctx written by earlier ones.
# check_evidence runs on both paths: it labels quotes when no corpus is loaded.
CHECKS = [
    (check_transitions, False),
    (check_actions, False),
    (check_hypothesis_count, False),
    (check_timing, False),
    (check_materiality, False),
    (check_evidence, False),
    (check_policy, False),
    (check_grounding, True),
    (check_duplicates, True),
    (check_mandatory_route, False),
    (check_quality, False),
]

__all__ = ["CHECKS"]
