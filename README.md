# Signal Labs — AI Engineer Take-Home

## About Signal Labs

Signal Labs builds AI-native infrastructure that decides what deserves attention: signal
detection, hypothesis reasoning, and automated decisioning at scale. Our view is that the
failure in most enterprise AI is architectural, not a model-capability problem. Models are
good enough. What is missing is a system that can look at everything an organisation
produces and correctly decide the small number of things a human should look at today.

That decision is the product. Getting it wrong in either direction is expensive, and the
two directions are expensive in different ways.

## The Role

You'll build evals for our attention systems. You'll identify where they fail, quantify how
often, and build the measurement systems that tell us whether a change actually helped.

This assignment tests the core skills: reading a formal specification and holding an agent to
it, discovering failure modes from data that is actively misleading, separating real signal
from pipeline artefacts, handling ambiguous ground truth, and building evaluations that
predict real-world outcomes rather than just correlating with themselves.

## Requirements

- Python 3.10+
- Any operating system
- A PDF reader (for the specification document)

## Guidelines

- Use any tools you want — LLMs, libraries, frameworks, whatever makes you effective.
- There are no right answers for many parts of this. Your reasoning matters more than your conclusions.
- Depth on fewer problems beats shallow coverage of everything.
- Questions? Reach out to darshanm@signallabs.ai

## What's in the Repo

```
signal-attention-evals-takehome/
├── README.md                         # This document
├── spec.pdf                          # Agent specification (READ THIS FIRST)
├── eval_takehome.py                  # Your evaluator goes here
├── data/
│   ├── signal_dossiers.jsonl         # 629 signal dossiers — the unit of evaluation
│   ├── telemetry.jsonl               # 32,032 account-day product metrics
│   ├── artifacts.jsonl               # 3,810 text artefacts (tickets, email, chat, CRM, …)
│   ├── accounts.jsonl                # 180 accounts: ARR, tier, flags, owner, renewal
│   ├── owners.jsonl                  # 23 CSMs and AEs: timezone, locale, channel
│   ├── outcomes.jsonl                # Renewal outcomes observed as of 2026-09-01
│   └── annotations/
│       ├── annotator_1.jsonl         # Quality labels from annotator 1
│       ├── annotator_2.jsonl         # Quality labels from annotator 2
│       └── annotator_3.jsonl         # Quality labels from annotator 3
└── docs/
    ├── domain.md                     # Glossary, policy rules, known data events
    └── data_dictionary.md            # Exact schema of every file
```

## The Specification

**Start here.** Read `spec.pdf` before looking at the data.

The specification defines the expected behaviour of our account-risk attention agent as a
state machine over the lifecycle of a *signal*. It includes:

- The set of valid states and transitions
- Actions and when they can be triggered
- Timing constraints, including per-severity time-to-attention targets
- Invariants that must hold, including evidence integrity
- Policy and containment requirements (some precise, some deliberately requiring judgment)
- Materiality rules, and rules about grounding numeric claims in the telemetry
- Quality expectations

Your job is to evaluate whether each dossier conforms to the specification, and to assess
whether the signal deserved the attention it got.

Then read `docs/domain.md`. It has the glossary, the policy rules, and — importantly — the
list of known data events in the telemetry pipeline. Several of the agent's most confident
claims are artefacts of those events. You will not find them by reading dossiers alone.

## The Data

### Signal Dossiers

629 dossiers from the Cartogram deployment. Each is the agent's complete record of one signal:

- **lifecycle**: the states the signal moved through (`from_state`, `to_state`, `trigger`, `at`)
- **hypotheses**: what the agent concluded, at what confidence, citing which evidence
- **evidence**: artefacts the agent attached, each with a quoted excerpt
- **metrics_claimed**: numeric claims the agent made, e.g. `"api_calls -61% week over week"`
- **actions**: what the agent did (`attach_evidence`, `request_enrichment`, `notify_owner`, `suppress`, …)
- **notifications**: when and how a human owner was paged
- **scoring**: severity, confidence, ARR at risk
- **decision**: disposition, recommended play, and whether that play is customer-visible
- **metadata**: account tier, ARR, materiality floor, owner timezone, account flags, renewal date

Not every signal a detector produced became a dossier. Detector coverage is itself something
you can measure.

### Telemetry

32,032 account-day rows across 181 days (2026-02-01 to 2026-07-31), six metrics per row.

This file is not clean, and it is not clean in ways that matter. Three of the distortions are
documented in `docs/domain.md`. Three are not. At least one of them will make a healthy
account look like it collapsed overnight, on the same date, across a large cohort of accounts
at once.

### Text Artefacts

3,810 artefacts across nine sources: support tickets, email threads, internal and shared chat,
CRM notes, meeting notes, survey responses, security reviews, billing events, and automated
monitoring posts.

Artefacts have typos, code-switching between languages, voice-to-text artefacts from calls,
templated severity fields, sarcasm, near-duplicates, quoted email history that repeats old
complaints under a new positive message, forwarded text naming *other* accounts, and contact
details in signature blocks. Sixteen artefacts are marked `restricted: true` and are subject
to a containment rule in the spec.

### Accounts and Owners

180 accounts with ARR, tier, region, industry, renewal date, materiality floor, and flags. Two
flags (`legal_hold`, `mna_quiet_period`) restrict what the agent is allowed to recommend.

23 owners with their timezone, locale and preferred channel. The notification-window rule in
the spec is relative to the *owner's* timezone, and owners span eight of them.

### Outcomes

Renewal outcomes observed as of 2026-09-01:

```json
{
  "signal_id": "sig_0042",
  "account_id": "acct_0114",
  "renewal_outcome": "downgraded",
  "arr_delta": -84000,
  "attribution": "uncertain",
  "concurrent_interventions": ["csm_outreach", "discount_offer"],
  "reached_human": true,
  "owner_acted": true,
  "owner_marked_useful": null,
  "customer_complained_about_outreach": false,
  "escalation_was_wasted": false,
  "post_hoc_root_cause": "budget_pressure"
}
```

Two things to be careful about.

`attribution` is `"uncertain"` for most outcomes where anything happened at all. Owners run
their own plays in parallel with whatever the agent said. Whether *this signal* changed the
outcome is usually not knowable from this data, and your evaluation needs to account for that
rather than assume it away.

`owner_marked_useful` is `null` for every signal that never reached a human — which is roughly
half the corpus. Owners cannot rate a signal they were never shown. If you use owner feedback
as a label, you are training on a sample selected by the very policy you are trying to
evaluate. Say what you did about that.

### Annotations

Three annotators independently labelled 240 dossiers each, with 130 dossiers labelled by all
three. They were given the same rubric and interpreted it differently.

```json
{
  "signal_id": "sig_0042",
  "deserved_attention": true,
  "quality_score": 0.62,
  "failure_points": [
    {"step": 3, "category": "fabricated_evidence", "severity": 0.8, "note": "..."},
    {"step": 6, "category": "wasted_attention",    "severity": 0.5, "note": "..."}
  ],
  "risk_flags": ["policy_concern", "evidence_integrity"],
  "overall_assessment": "Right instinct, sloppy execution.",
  "_annotator": "annotator_1"
}
```

They disagree on **31%** of the three-way overlap on `deserved_attention` alone, and their
per-category emphasis differs sharply. This is genuine ambiguity about what "deserved
attention" means when attention is scarce — not annotator sloppiness. How you handle the
disagreement is important. Note in particular that the three do not disagree at random: each
one is internally consistent about something different.

## What You Need to Build

### 1. An Evaluator

Build a `SignalEvaluator` class in `eval_takehome.py`. Given a dossier and the specification,
it should produce a quality score, a risk assessment, a judgment about whether the signal
deserved attention, and specific spec violations:

```python
class SignalEvaluator:
    def load_context(self, accounts, owners, telemetry, artifacts, dossiers):
        """Optional. Called once before evaluation, if you implement it."""

    def evaluate(self, dossier: dict) -> dict:
        return {
            "quality_score": float,         # 0-1, higher = better dossier
            "risk_score": float,            # 0-1, higher = more likely to cause harm
            "deserved_attention": bool,     # should this have reached a human at all?
            "violations": [
                {
                    "step": int,
                    "rule": str,            # which spec rule was violated
                    "severity": float,      # 0-1
                    "explanation": str
                }
            ]
        }
```

Some spec rules cannot be checked from a dossier alone. Evidence integrity needs
`artifacts.jsonl`, quantitative grounding needs `telemetry.jsonl`, and the duplicate-signal
rule needs the other dossiers. `load_context` is how you get all of it. It is optional, and we
will call it once before evaluating if you implement it — passing the dossier set we are about
to ask you about. Your `evaluate()` must still return something sensible if it was never
called.

Your evaluator should be self-contained — it will be run on our machine against additional
dossiers not included in this repo. Do not make external API calls inside `evaluate()`.

### 2. A Violation Report

Analyse the 629 dossiers against the specification. Create `violations.md` documenting:

- What types of spec violations are most common
- Which violations correlate with bad outcomes (complaints, wasted escalations, churn)
- Which violations are common but harmless, and how you established that
- Statistical analysis: violation rates by account tier, region, detector, and owner
- Specific dossier examples with evidence

Be specific — reference spec sections, step numbers, signal IDs and artefact IDs.

### 3. An Attention Budget Analysis

Cartogram has 14 CSMs. Each absorbs about **5 investigated signals per week** — that is the
constraint, and it is per-owner, not just in aggregate. The corpus spans 21 weeks.

The agent's actual operating point over that period: it routed **294 of 629** signals to a
human. Signal volume is extremely uneven — the median week produces 21 candidate signals and
the worst produces 107, which is more than the whole team can absorb in a week no matter how
it is distributed. Four weeks account for a wildly disproportionate share of everything the
agent raised.

Create `attention_budget.md`, at most one page, answering:

- Why is the load so bursty? Name the cause of each burst week, with evidence.
- Under a per-owner budget of 5 signals per week, which signals should have been routed?
  Produce a ranking, not a classifier — attention is allocated, not labelled.
- How much better than the agent's actual operating point is your policy, and by what measure?
  State the agent's precision and recall as your baseline, and be explicit about what you
  counted as a signal that deserved attention and why.
- What does your policy cost in missed risk, and how confident are you in that number given
  what `attribution` does and does not tell you?

A policy that just filters out the burst weeks is not an answer. Some real risk is in there.

### 4. A Writeup

A **4-page maximum** document (`writeup.md`) covering:

- **Methodology** — How did you approach this? How did you map spec rules to dossier data?
- **The data** — What did you find in the telemetry, and what did you do about it?
- **Annotator disagreement** — How do the three annotators differ? How did you handle it?
- **Findings** — What did you learn about the agent's behaviour? What predicts harm?
- **Limitations** — Where does your evaluation fail? What would you need to do better?
- **If you had 3 months** — How would you build a production eval system for an attention
  layer? How would you close the loop from eval to agent improvement?

The writeup is weighted heavily. Clear thinking with simple code beats complex code with no
explanation.

## Submission

Send the following to darshanm@signallabs.ai:

- A GitHub repo with your `eval_takehome.py`, `violations.md`, `attention_budget.md`,
  `writeup.md`, and any supporting analysis
- A 5-minute Loom recording walking through your approach and key findings

## What We're Looking For

- **Spec reasoning** — Can you map a formal specification onto messy, adversarial agent traces?
- **Data scepticism** — Do you check whether a number means what it appears to mean?
- **Analytical rigour** — Do you form hypotheses and test them, or pattern-match and guess?
- **Statistical literacy** — Do you understand confounders, selection effects, correlation vs.
  causation, and inter-annotator agreement?
- **Domain reasoning** — Do you understand *why* a particular failure is expensive here, and
  why two failures with the same rate can differ in cost by an order of magnitude?
- **Eval design** — Do your metrics measure something meaningful, or just something easy?
- **Communication** — Can you explain complex findings clearly?

---

*Cartogram Inc. is a fictional company. Every account, person, message and metric in this
corpus is synthetic. No real customer data is present.*
