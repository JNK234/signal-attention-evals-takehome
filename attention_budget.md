# Attention budget

**The agent's problem was selection, not volume.** Cartogram has 14 customer success managers (CSMs). Each can investigate about 5 signals a week. Over 21 weeks the agent routed 294 of 629 signals to a human. The cap of 5 almost never forced a choice.

**A policy that routes only what the spec obliges does better on every outcome measure.** It routes 189 signals instead of 294. Among routed signals, later churn or downgrade rises from 30% to 39%. Wasted escalations fall from 115 to 29. Complaints fall from 16 to 4. The policy leaves $3.02M of realised loss unreached, almost all at accounts the agent reached and lost anyway. The script `analysis/attention_budget.py` prints every number in this document.

## Why is the load so bursty? Name the cause of each burst week, with evidence.

**Six weeks stand out, and each one is a single Sunday sweep hitting a whole cohort.** Grouping dossiers by ISO week of `opened_at` reproduces the README: median 21 signals a week, maximum 107. The table shows the six weeks with 40 or more signals, about twice the median. "Sunday" is how many opened on that week's Sunday. "Real" and "artefact" are the verdict of rule M6, which recomputes each claimed drop on corrected telemetry.

| week | signals | Sunday | detector and metric | cohort hit | real | artefact | cause |
|---|---|---|---|---|---|---|---|
| W14 (Apr 5) | 40 | 35 | seat_decay, `dau_seats` | mixed | 19 | 7 | Good Friday, Easter Monday |
| W18 (May 3) | 55 | 44 | seat_decay, `dau_seats` | mixed | 16 | 13 | May 1 Labour Day |
| W21 (May 24) | 65 | 59 | usage_cliff, `api_calls` | 50 of 65 on `legacy` | 3 | 43 | May 18 migration |
| W22 (May 31) | 50 | 36 | seat_decay, `dau_seats` | namer | 15 | 12 | Memorial Day |
| W24 (Jun 14) | 107 | 95 | usage_cliff, `dau_seats` | 86 of 107 apac/emea | 4 | 72 | Jun 11–13 ingest gap |
| W27 (Jul 5) | 47 | 32 | seat_decay, `dau_seats` | namer | 14 | 8 | Jul 3 (Independence Day) |

**The mechanism is the same in every row.** The detectors run one sweep a week over a trailing 7-day window. Each compares an account only to its own previous week. So a drop shared by many accounts fires on all of them in the same sweep. The four largest weeks hold 44% of the corpus.

**Two weeks are pipeline artefacts. The drops are not real.**

- **Week 21 is the May 18 pipeline migration.** Before that date, `api_calls` on the `legacy` collector were recorded at twice their true value. The fix halved the recorded series while real traffic did not move. The May 24 sweep compared a corrected week against an inflated one. 43 of the claimed drops vanish after M6 corrects the double count. 3 are real.
- **Week 24 is the June 11–13 ingest incident.** Rows for apac and emea accounts (Asia-Pacific, and Europe, Middle East and Africa) are absent for three days. 72 of the claimed drops only reproduce if the missing days count as zero usage. 4 are real.

**Four weeks are holidays. The drops are real and benign.** Three lines of evidence agree. First, the drops follow national calendars in the telemetry. On Memorial Day the median North American (namer) account fell from 37.9 active users to 7.8, while emea held at 58. On July 3, namer fell from 27.5 to 6.9 with emea flat. Second, activity metrics fell together while error rate (0.4–0.5%) and p95 latency did not move. That pattern is absence, not failure. Third, the customers said so. 306 artefacts mention a holiday or being out of office, and `art_00771` reads "do not read anything into the usage numbers that week."

**Week 18 looks like a pipeline event but is not.** It contains the April 27 latency re-instrumentation. Its claims are about seats, not latency, and only 13 of 55 fail M6. The cause is May 1 Labour Day. The agent labelled 40 of the 55 signals benign and still routed 22 of them.

**Two things stay open.** The namer weekday median stayed low for two weeks after July 3, from 36.5 before to 28.6 after. A one-day holiday does not explain that. The apac weekly curve is shifted one day by UTC-day bucketing, so I excluded apac from the regional comparisons.

## Under a per-owner budget of 5 signals per week, which signals should have been routed? Produce a ranking, not a classifier — attention is allocated, not labelled.

**The cap rarely binds, so the ranking's real job is a floor.** An owner-week is one CSM in one ISO week. 245 owner-weeks contain at least one signal. Candidates exceed 5 in only 23 of them, nearly all in burst weeks. No owner-week holds more than 5 signals that clear the floor below. Filling every free slot would route 568 signals, and those churn at the 29% base rate.

**Every signal goes into one of four tiers.** The tier is the first key of the ranking.

- **Tier 1 (163 signals): the spec obliges a human.** The evidence holds a confirmed §8.1 trigger, such as a written cancellation, legal reference or billing dispute. Or the enrichment request timed out under §4.6. Or the agent itself scored the signal P0 or P1 with a non-benign explanation. Each condition is a sentence in the spec, not my judgment.
- **Tier 2 (26 signals): the decline is real but the spec does not oblige a route.** The claimed drop survives M6. The hypothesis is not benign. The account's region did not move with it. These are the README's "real risk in the burst weeks."
- **Tier 3 (277 signals): everything else.** The drop could not be verified, or the agent's own explanation was benign, or there was no numeric claim.
- **Tier 4 (163 signals): artefacts and region-wide dips.** These rank last but are not deleted, so a real signal in week 21 or 24 still competes on its merits.

**Within a tier the order is severity, then renewal date, then contract size.** Severity is the agent's label, P0 before P1 before P2. Renewal date is days to renewal, soonest first. Contract size is `arr_annual` from `accounts.jsonl`. I never use the agent's own `arr_at_risk`, which is wrong on 61 dossiers. No weight is fitted.

**The policy routes tiers 1 and 2, at most 5 per owner-week.** That comes to 189 signals. The full ranking is `analysis/attention_budget_ranking.csv`. Because eligible signals never exceed 5 in any owner-week, this corpus does not test the within-tier order under overload.

**One CSM's week shows how the tiers become a top 5.** Owner `u_011` had 8 candidates in week 24. Four are tier 1. One is a P1 usage cliff on a $1.67M account 16 days from renewal. Two are signals on a $237K account that both carry a written legal reference. One is a billing dispute. One is tier 2, a real seat decline on a $190K account. Three are below the line: an unverifiable claim and two ingest-gap artefacts. The policy routes the five and fills the slots exactly. The agent routed 3 of the 8 and suppressed both legal-reference signals, which §8.1 says must reach a human.

**The ordering carries information, and so does the agent's.** I cut four orderings at the same 189 signals and counted later churn or downgrade. This ranking: 39%. The agent's own severity label: 40%. Risk score times ARR: 31%. Random: 28%. The agent orders about as well as I do. The gain comes from routing fewer signals, not from ordering them better.

## How much better than the agent's actual operating point is your policy, and by what measure? State the agent's precision and recall as your baseline, and be explicit about what you counted as a signal that deserved attention and why.

**A signal deserved attention if any one of three spec conditions holds.** The evaluator checks them in this order and records the first that matches, so the counts sum to 163 of 629.

1. **§8.1, a confirmed mandatory-route trigger in the evidence (54 signals).** The spec names five triggers. They are intent to cancel, a legal reference, a security incident, a billing dispute of at least 5% of ARR, and a champion or economic-buyer departure within 90 days of renewal. A trigger counts only if the artefact exists, belongs to this account, is quoted verbatim, and was written by a customer. The classifier must also read the current text, not quoted history, as that trigger. By kind: 27 billing disputes, 12 legal references, 9 departures, 3 cancellations, 3 with two triggers.
2. **§4.6, the enrichment request timed out (52 signals).** The agent asked for more data and did not get it in time. The spec says such a signal must still reach a human.
3. **§6.3, the agent scored the signal P0 or P1 and committed to a non-benign hypothesis (57 signals).** The spec defines P0 as "act today" and P1 as "material risk with corroboration," so this is the agent recording that a human was needed. A champion-departure hypothesis must also sit within the 90-day renewal window. This condition is an inference from the spec's severity table, not a quoted obligation, and I flag it as such.

**How I arrived at this definition.** I split the annotated dossiers into a development and a held-out set and tested candidate rules against the annotators' majority vote. The two quoted obligations alone (§8.1 and §4.6) agreed with the majority at 0.30 on the development set. Adding §6.3 raised that to 0.44, at a permutation-test p of 0.026, and kept all 18 hand-derived spec verdicts. The strongest single predictor on held-out data was the agent's `arr_at_risk`. I rejected it. The agent writes that field, and it is wrong on 61 dossiers. A self-reported number must not be able to make a spec-required route look undeserved. The definition reads nothing from the routing decision, the outcomes, or the annotators, so it can be scored against all three.

**Outcomes corroborate the definition without having built it.** Tier 1 signals churn or downgrade at 39%. The rest churn at 24%. Tier 2 signals are routed but not counted as deserved, because the spec does not require them.

**The agent's baseline is precision 31% and recall 56%.** Its 294 routed signals contain 91 deserved ones. Seven of every ten signals it put in front of a CSM did not need a human. It still failed to route 72 of the 163 that did. All 72 were suppressed (60) or expired (12). 31 carried a written §8.1 trigger, 30 had timed out waiting for enrichment, and 11 were the agent's own P0/P1 calls.

**My policy scores precision 86% and recall 100% on that yardstick, by construction.** The rule that defines deserved is the rule that fills tier 1. The 14 points of precision given up are the 26 tier 2 signals. These numbers prove consistency, not correctness.

**The independent measure is what happened to the accounts afterwards.** These are retrospective comparisons on one corpus. They show the policy selected signals that were more often real, not that deploying it would have changed an outcome.

| measure | agent | policy |
|---|---|---|
| signals routed | 294 | 189 |
| routed signals that later churned or downgraded | 30% | 39% |
| customer complaints | 16 | 4 |
| escalations owners marked wasted | 115 | 29 |
| lost ARR captured | 76% | 66% |

**Against the three annotators, the policy is more precise and recalls less.** On the 130 signals all three labelled, the policy has precision 0.38 and recall 0.39. The agent has 0.31 and 0.61. The annotators said yes to 43% of P1 signals and 6% of P2 signals, so they follow the severity label. They also reward a convincing story without a spec trigger. My definition refuses both, which is the source of the recall gap.

**Two limits sit on the yardstick.** 48 written mandatory triggers never became a dossier, so no ranking can reach them. Tier 1 reads the agent's own evidence and severity, so a trigger the agent never attached counts as undeserved. The 27 such dossiers are unrouted at 44% against 53% for the corpus, so the bias has no clear direction.

## What does your policy cost in missed risk, and how confident are you in that number given what `attribution` does and does not tell you?

**The policy leaves $3.02M of realised loss unreached.** 16 accounts churned or downgraded with no signal clearing the floor. Total loss across 62 accounts is $11.66M. The agent reached 14 of the 16 ($2.93M) and lost them anyway. The reverse, accounts I reach that the agent did not, is 4 ($0.23M). `outcomes.jsonl` is per signal and 92 of 176 accounts carry contradictory outcomes, so I count each account's loss once at its worst recorded outcome.

**`attribution` says routing did not decide these losses.** The field records whether the outcome is linked to this signal: certain, uncertain or none. On the 26 missed signals it is uncertain on 17 and none on 9, never certain. 17 had a discount, executive call or roadmap letter running alongside. Owners marked 7 of the 11 the agent routed as useful, then lost the account. So $3.02M is the most routing could have touched. The recoverable part is much smaller, and the data cannot say how much.

**The larger exposure runs the other way.** 19 renewals carry certain attribution, which credits the signal with the save. 4 of those accounts ($1.7M ARR) get no routed signal under my policy.

**Confidence is fair on the direction and low on the dollar figure.** `attribution` is uncertain for most outcomes where anything happened, so none of these figures is a causal estimate.
