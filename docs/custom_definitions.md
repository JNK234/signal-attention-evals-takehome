# Names this evaluator coins

Every identifier the evaluator emits that is **not** a rule the specification numbers itself. The
spec numbers three families — `I1`–`I6` (§7 invariants), `M1`–`M6` (§9 materiality), `Q1`–`Q5`
(§10 quality) — and everything else in `violations[].rule` is the section that holds the rule
(`§4.8` for Table 6, `§5` for Table 7, `§6.1`–`§6.4` timing, `§8.1`–`§8.7` policy), so those need
no entry here. What follows is the rest.

Two rules for this file:

1. **Names are unique across all three surfaces** — `violations[].rule`, `_facts.deserved_reason`,
   `_facts.risk_conditions`. A name means one thing wherever it appears.
2. **Every entry states its authority**: quoted from the spec, cited from `docs/domain.md`, or our
   own inference. An inference is allowed; an unsourced name is not.

`tests/test_scoring.py::test_every_coined_name_is_documented` fails if a name is added to the code
without an entry here. Four of the five below accumulated before that test existed.

---

## `UNEVALUATED` — `violations[].rule`, severity 0.0

**What it is.** A meta marker, not a violation. When the NLI labeller is unavailable, or every
non-bot evidence artefact came back unreadable, one entry names the rules that went unchecked
(§8.1 text triggers, Q2/I5 hypothesis fit, Q4 sarcasm) and over how many artefacts.

**Why it exists.** The grader reads `violations[]`. Returning nothing would make an unreadable
dossier indistinguishable from a clean one — silence reading as a pass. Severity is 0.0 and
`spec.META_RULES` excludes it from `quality_score`, so it reports without scoring.

**Authority.** Ours. The spec has no concept of "could not evaluate"; this is the honest way to
say so inside a contract that only has room for violations.

**Caveat for a grader.** This is the one entry that rides in `violations[]` with a rule id the
spec does not define. A consumer that looks up every `rule` in its own table will not find it.

## `A8` — `_facts.deserved_reason`

**What it is.** The cold-path row of the `DESERVES` table. When `load_context` was never called
there is no artefact corpus, so an author cannot be confirmed and a quote that reads as a §8.1
trigger can only ever be *uncertain*. On that path alone, uncertain counts.

**Why it exists.** The alternative is that an uncalled `load_context` silently makes every signal
undeserving — a worse failure than over-reporting on the path we already warn about.

**Authority.** Ours, and marked as judgment in the provenance guard's allow-list
(`tests/test_scoring.py`, `JUDGMENT_ROWS`).

## `§6.3` — `_facts.deserved_reason`

**What it is.** The agent assigned severity `P0` or `P1` **and** committed to a hypothesis that is
not `benign_variation` or `no_hypothesis`. If that hypothesis is `champion_departure`, renewal must
also be within 90 days.

**Why it exists.** §6.3's table is the spec's own statement of what each severity level *means* —
P0 "written churn or legal intent; act today", P1 "material risk with corroboration", P2 "worth a
look this week". So a dossier stamped P0/P1 with a real explanation is the agent recording that a
human was needed, which is exactly the question `deserved_attention` asks. The README asks us to
assess deserving as a task distinct from conformance (l.75-76) and to state "what you counted as a
signal that deserved attention and why" (l.257).

**The window clause is what makes it admissible.** §8.1 bullet 4 bounds the departure trigger to
"within 90 days of renewal", so a claimed departure outside it is deliberately not a mandatory
route. Without the clause this row overrides that bound and contradicts two hand-derived golden
verdicts (`sig_0059` at 197 days, `sig_0441` at 109).

**Authority.** Inference from §6.3's severity table, not a quoted "must" like §8.1 and §4.6. The id
is the section it reasons from, so nothing new is invented; the reference string says plainly that
it is an inference. Measured: dev alignment against annotator majority 0.296 → 0.438, permutation
p = 0.026, all 18 goldens preserved.

**What it deliberately does not read.** `arr_at_risk`. Tested and rejected on measurement rather
than principle — it carries the strongest held-out signal available (arr ≥ 10% of annual: dev
0.619, held 0.483, p = 0.000), but AND'ing it onto the table lets the agent's own figure veto a
spec obligation (`sig_0350` is a golden True via §4.6 with arr_at_risk at 7.6% of annual), and the
agent misstates the figure on 59 of 629 dossiers — 19 impossible under M1, 42 inconsistent under
M5. Reading a self-report to *add* coverage is safe; reading one to *withhold* a route the spec
requires is not.

## `VIS_UNDESERVED` — `_facts.risk_conditions`

**What it is.** A risk condition: the dossier recommended a customer-visible play on a signal no
spec condition required.

**Authority.** `docs/domain.md` states the mechanism — complaints concentrate on customer-visible
plays against accounts that were never at risk — without ranking it, so the magnitude is judgment.

## `UNROUTED_DESERVED` — `_facts.risk_conditions`

**What it is.** A risk condition: the signal deserved a human and never reached one.

**Authority.** `docs/domain.md` prices the other error direction ("a false negative costs the
ARR") but names no rule for it, so this is judgment.

**Coupling worth knowing.** Both this and `VIS_UNDESERVED` read `deserved_attention`, so changing
that key rewrites risk scores. Adding the `§6.3` row moved 46 of them: 40 dossiers lost
`VIS_UNDESERVED` (risk down) and 6 gained `UNROUTED_DESERVED` (risk up). Complaint concentration in
the top half of the risk ranking held at 15/16.
