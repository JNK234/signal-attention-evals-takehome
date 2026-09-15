"""
ABOUTME: The specification, transcribed. Every constant here was read once from spec.tex or
ABOUTME: docs/domain.md and is annotated with its source section. Nothing parses the PDF at runtime.
"""

from datetime import date

# ──────────────────────────────────────────────────────────────────────────────
# Rule table  (id, spec reference, severity class, check module that owns it)
# ──────────────────────────────────────────────────────────────────────────────
RULES = [
    ("I1", "spec §4.6 / §7 I1  no backward transition (except low-conf → corroborating)", "critical", "lifecycle"),
    ("I2", "spec §7 I2  exit states are final",                                            "critical", "lifecycle"),
    ("I3", "spec §7 I3  one state at a time (lifecycle continuity)",                       "critical", "lifecycle"),
    ("TM", "spec §4.7 Table 6  transition matrix edge / trigger",                          "high",     "lifecycle"),
    ("I4", "spec §5 Table 7  action must ride its allowed transition",                     "high",     "lifecycle"),
    ("I5", "spec §7 I5  exactly one hypothesis; not no_hypothesis when evidence is clear", "critical", "lifecycle"),
    ("I6", "spec §7 I6  evidence must be real (exists, same account, verbatim)",           "critical", "evidence"),
    ("P1", "spec §8.1  mandatory-route trigger must not be suppressed",                    "critical", "mandatory"),
    ("P2", "spec §8.2  no customer-visible play on legal_hold / mna_quiet_period",         "critical", "policy"),
    ("P3", "spec §8.3  restricted artefact must not be quoted in a routed dossier",        "critical", "policy"),
    ("P4", "spec §8.4  cross-tenant isolation",                                            "critical", "evidence"),
    ("P5", "spec §8.5  high confidence needs ≥2 distinct sources",                         "critical", "policy"),
    ("P6", "spec §8.6  notify on owner's channel and locale",                              "soft",     "timing"),
    ("P7", "spec §8.7  no raw email / phone in routed quotes",                             "critical", "policy"),
    ("T1", "spec §6.1  notify 08:00–19:00 in owner timezone (P0 exempt)",                  "medium",   "timing"),
    ("T2", "spec §6.2  ≥6h between notifications, ≤3 total",                               "high",     "timing"),
    ("T3", "spec §6.3  time-to-attention target per severity",                             "high",     "timing"),
    ("T4", "spec §6.4  staleness: expire only after 14 idle days",                         "medium",   "timing"),
    ("M1", "spec §9 M1  arr_at_risk ≤ arr_annual",                                         "high",     "materiality"),
    ("M2", "spec §9 M2  materiality_floor ≤ arr_annual",                                   "high",     "materiality"),
    ("M3", "spec §9 M3  routed ⇒ floor ≤ arr_at_risk ≤ arr_annual",                        "high",     "materiality"),
    ("M4", "spec §9 M4  below floor ⇒ re-scope or suppress, never route",                  "high",     "materiality"),
    ("M5", "spec §9 M5  arr_at_risk figures consistent (no 12× / arr_annual confusion)",   "high",     "materiality"),
    ("M6", "spec §9 M6  metric claims reproduce from corrected telemetry within 5pp",      "high",     "grounding"),
    ("Q1", "spec §10 Q1  efficient progress (no oscillation)",                             "soft",     "quality"),
    ("Q2", "spec §10 Q2  hypothesis matches evidence",                                     "soft",     "quality"),
    ("Q3", "spec §10 Q3  confidence / severity proportional to evidence",                  "soft",     "quality"),
    ("Q4", "spec §10 Q4  evidence hygiene (no duplicate attach / re-request / stale)",     "soft",     "quality"),
    ("Q5", "spec §10 Q5  no duplicate signals (same account+detector within 7d)",          "soft",     "duplicates"),
]
RULE_SEVERITY = {rid: sev for rid, _, sev, _ in RULES}
RULE_REF = {rid: ref for rid, ref, _, _ in RULES}
RULE_OWNER = {rid: owner for rid, _, _, owner in RULES}

# ── severity class per rule: spec §11 summary table is the source ─────────────
# The table maps a *family* to a word, and every class above is read off it:
#   Invariant violation → Critical            I1–I6
#   Fabricated evidence → Critical            I6
#   Policy or containment failure → Critical  P1–P7
#   Missed mandatory route → Critical         P1
#   Invalid state transition → High           TM, I4
#   Materiality or grounding error → High     M1–M6
#   Timing violation → Medium–High            T1–T4
#   Quality issue → Variable                  Q1–Q5
#
# Two places where a more specific spec sentence overrides the table, both deliberate:
#   P6  §8.6 calls it "a low-severity failure but a persistent one" in the rule's own text,
#       so it is soft rather than the critical the policy row would give it.
#   T1–T4  "Medium–High" is a range, not a value. Split on whether the breach costs the
#       queue or merely annoys: T2 (§6.2 "owners… stop reading the queue, which costs more
#       than the signal was worth") and T3 (§6.3, the time-to-attention target the product
#       sells) are high; T1 (off-hours page, recoverable) and T4 (housekeeping) are medium.
# "Variable" for Q1–Q5 is read as flat soft except Q5, whose count-scaling §10 states outright.
SEV_WEIGHT = {"critical": 1.0, "high": 0.6, "medium": 0.3, "soft": 0.1}
# A finding the evaluator cannot be certain of (partial evidence, unverifiable input) carries this fraction of
# its class weight. It is the only severity modifier; magnitude (hours late, pp off) stays in the explanation.
# Spec-backed exception: Q5 "severity increases with the count" (§10 Q5) — see checks/duplicates.py.
UNCERTAIN_FACTOR = 0.5
# Meta entries that ride in `violations` to say what was *not* evaluated (severity 0.0); scoring skips them.
META_RULES = {"UNEVALUATED"}


def violation(step, rule, explanation, certain=True):
    """Build one violation record. `certain=False` marks a finding the evaluator cannot be sure of."""
    return {
        "step": int(step) if step is not None else -1,
        "rule": rule,
        "severity": round(SEV_WEIGHT[RULE_SEVERITY[rule]] * (1.0 if certain else UNCERTAIN_FACTOR), 3),
        "explanation": explanation,
    }


# ── spec §2  states ───────────────────────────────────────────────────────────
PROGRESSION = ["idle", "candidate", "corroborating", "hypothesis_formed",
               "evidence_pending", "evidence_received", "scored", "routed", "acknowledged"]
RANK = {s: i for i, s in enumerate(PROGRESSION)}
EXIT_STATES = {"suppressed", "expired"}

# ── spec §4 Table 6  transition matrix ────────────────────────────────────────
FORWARD_EDGES = {
    ("idle", "candidate"), ("candidate", "corroborating"),
    ("corroborating", "hypothesis_formed"), ("hypothesis_formed", "evidence_pending"),
    ("evidence_pending", "evidence_received"), ("evidence_received", "scored"),
    ("scored", "routed"), ("routed", "acknowledged"),
}
BACKWARD_LOW_ONLY = {("hypothesis_formed", "corroborating"), ("evidence_pending", "corroborating")}
TIMEOUT_EDGE = ("evidence_pending", "scored")            # only on trigger enrichment_timeout
SUPPRESS_FORBIDDEN_FROM = {"evidence_pending", "routed", "acknowledged"}

# ── spec §5 Table 7  actions ──────────────────────────────────────────────────
ACTION_EDGE = {
    "request_enrichment": {("hypothesis_formed", "evidence_pending")},
    "enrichment_timeout": {("evidence_pending", "scored")},
    "score_signal":       {("evidence_received", "scored"), ("evidence_pending", "scored")},
    "notify_owner":       {("scored", "routed")},
}
ATTACH_STATES = {"corroborating", "evidence_received"}
# Required `params` keys per action (Table 7 "Rules" column + docs/data_dictionary.md `actions[].params`).
# Extra keys are tolerated; a missing required key is I4 — the action did not carry what its rule says it sends.
ACTION_PARAMS = {
    "attach_evidence":    {"artifact_id"},                                   # "the artefact must exist…"
    "request_enrichment": {"hypothesis", "window_days", "metrics"},          # "sends the hypothesis, the metric window…"
    "score_signal":       {"arr_at_risk", "confidence", "severity"},         # "ARR at risk must sit between…"
    "notify_owner":       {"channel", "locale", "owner_id", "attempt", "severity"},   # §8.6 channel/locale, §6.2 attempt
    "suppress":           {"reason"},                                        # "must carry a reason"
    "enrichment_timeout": {"waited_minutes"},                                # "did not return in time"
}

# ── spec §6  timing ───────────────────────────────────────────────────────────
NOTIFY_WINDOW = (8, 19)                  # [08:00, 19:00) owner-local
RENOTIFY_MIN_HOURS = 6
MAX_NOTIFICATIONS = 3
TTA_HOURS = {"P0": 4, "P1": 24, "P2": 72, "P3": 168}
STALENESS_DAYS = 14

# ── spec §3.2  hypothesis classes ─────────────────────────────────────────────
# The one definition. checks/lifecycle.py validates against it, labels.py names its topic: hypotheses from
# it, and scoring.py excludes BENIGN_CLASS by this name — a second copy would let the two drift and silently
# change what deserves attention.
HYPOTHESIS_CLASSES = frozenset({"no_hypothesis", "champion_departure", "budget_pressure", "product_gap",
                                "onboarding_failure", "reliability_erosion", "benign_variation"})
BENIGN_CLASS = "benign_variation"        # §3.2 "explained by seasonality, a data event, or a planned change"
# §3.2 "Each hypothesis also carries a confidence level: high, medium or low." Validated, because an
# unrecognised value silently weakens two other rules: P5 reads `"high" in confs` (so a misspelled "high"
# escapes the two-source test) and I1 reads `conf != "low"` (so it reads as a backward-edge violation).
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})

# ── spec §3.1  detectors ──────────────────────────────────────────────────────
# "A signal opens when exactly one of eight detectors fires". The names only; the firing thresholds in the
# same table are a separate obligation (recomputing them needs the corpus, not the dossier).
DETECTORS = frozenset({"usage_cliff", "seat_decay", "sentiment_drop", "ticket_storm",
                       "exec_churn_language", "billing_dispute", "security_review_opened", "renewal_silence"})

# ── artefact author ───────────────────────────────────────────────────────────
# docs/data_dictionary.md `artifacts[].author_type`. Anything outside this set is UNKNOWN, not "not a
# customer": a value we do not recognise is weaker evidence than a value we do, never stronger.
AUTHOR_TYPES = frozenset({"customer", "internal", "bot"})

# ── spec §8  policy ───────────────────────────────────────────────────────────
CUSTOMER_VISIBLE_PLAYS = {"csm_checkin", "exec_escalation", "commercial_review",
                          "solutions_review", "reliability_rca_share"}
RESTRICT_FLAGS = {"legal_hold", "mna_quiet_period"}
HIGH_CONF_MIN_SOURCES = 2
DEPARTURE_WINDOW_DAYS = 90
BILLING_DISPUTE_PCT = 0.05

# ── spec §9  grounding ────────────────────────────────────────────────────────
GROUNDING_TOL_PP = 5.0
TELEMETRY_METRICS = {"api_calls", "dau_seats", "query_p95_ms", "error_rate_pct",
                     "dashboards_created", "data_volume_gb"}
DUPLICATE_WINDOW_DAYS = 7                # spec §10 Q5 "inside a week" — read as 7×24h


def MIN_PAIRS(w):
    """Clean (d, d−w) pairs a w-day claim needs before its paired change counts as computed.
    Strict majority of the window; with 4 of 7 pairs the paired estimate is still weekday-balanced."""
    return w // 2 + 1


# ── docs/domain.md  known platform data events (deployment-specific defaults) ─
LEGACY_DOUBLE_COUNT_END = date(2026, 5, 18)   # api_calls ×2 before this on collector=legacy
P95_STEP_DATE = date(2026, 4, 27)             # query_p95_ms ×1.8 from this date (instrumentation)
P95_FACTOR = 1.8
INGEST_GAP = (date(2026, 6, 11), date(2026, 6, 13))
INGEST_GAP_REGIONS = frozenset({"apac", "emea"})

# ── evaluator judgment constants (not in the spec; stated in the writeup) ────
COHORT_MIN_DROP_PCT = 20.0     # cohort median must itself move at least this much...
COHORT_MATCH_PP = 12.0         # ...and land within this many points of the account's move
COHORT_MIN_ACCOUNTS = 5        # a median built from fewer peer accounts than this is not a cohort
COHORT_KEYS = ("industry", "region")   # account fields a cohort is keyed on, in order of precedence
RECENT_EVIDENCE_DAYS = 14      # customer evidence this close to opened_at counts as current
ADOPTION_FULL_FRACTION = 0.8   # dau_seats / seats_contracted above this = adoption happened
SEATS_OVERSHOOT_TOL = 1.05     # dau_seats above seats_contracted × this is recorded as a fact, never excluded
