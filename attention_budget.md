# Attention budget

**The agent's problem was selection, not volume.** Cartogram has 14 customer success managers (CSMs). Each can investigate about 5 signals a week. Over the 21 weeks in this corpus the agent routed 294 of 629 signals to a human. That cap of 5 forced a choice in only 2 of 245 owner-weeks.

**A policy that routes spec obligations plus verified declines trades coverage for precision.** It routes 189 signals instead of 294. Among routed signals, the share that later churned or downgraded rises from 30% to 39%. On the 108 signals both the agent and the policy route, complaints are 3.7% against the agent's 5.4% overall, and wasted escalations 27% against 39%. The policy reaches accounts carrying 74% of realised loss. The agent reached 97%. Section 4 prices that gap.

**Sources.** `analysis/attention_budget.py` prints every number in sections 2 to 4 and the telemetry and artefact figures in section 1. It reads the latest evaluator run, so on a fresh clone run `python analysis/run_all.py` first (about two minutes, from the committed label cache). Two figures come from other analyses and are cited as such: the 48 undetected triggers (`analysis/detector_coverage.py`) and the definition-development figures (`analysis/deserved_split.py`, recorded 2026-09-16).

## Why is the load so bursty? Name the cause of each burst week, with evidence.

**Six weeks hold 40 or more signals, about twice the median, and each is dominated by one Sunday.** Grouping dossiers by the ISO week of their opening timestamp `opened_at` reproduces the README: median 21 signals a week, maximum 107. The README's four large weeks are W24, W21, W18 and W22, and together they hold 44% of the corpus. The table lists the six weeks at or above 40. "Sunday" is how many opened on that week's Sunday. "Real" and "artefact" count numeric claims under rule M6, the spec's grounding rule. M6 recomputes each claimed change on corrected telemetry and accepts it within five percentage points. Rows do not sum to the signal count. Some claims reproduce on neither series, and some dossiers carry no numeric claim.

| week | signals | Sunday | detector and metric | cohort hit | real | artefact | cause |
|---|---|---|---|---|---|---|---|
| W14 (Apr 5) | 40 | 35 | seat_decay on `dau_seats` | emea 23 of 40 | 19 | 7 | Good Friday, Apr 3 |
| W18 (May 3) | 55 | 44 | seat_decay on `dau_seats` | emea 26 of 55 | 16 | 13 | Labour Day, May 1 |
| W21 (May 24) | 65 | 59 | usage_cliff on `api_calls` | `legacy` 50 of 65 | 3 | 43 | May 18 migration |
| W22 (May 31) | 50 | 36 | seat_decay on `dau_seats` | namer | 15 | 12 | Memorial Day, May 25 |
| W24 (Jun 14) | 107 | 95 | usage_cliff on `dau_seats` | apac and emea 86 of 107 | 4 | 72 | Jun 11–13 ingest gap |
| W27 (Jul 5) | 47 | 32 | seat_decay on `dau_seats` | namer | 14 | 8 | Independence Day, Jul 3 |

Terms in the table. `dau_seats` is the number of distinct users active on an account that day. `api_calls` is the account's daily request count. seat_decay and usage_cliff are the two telemetry detectors, firing on a 29% and a 34% week-over-week fall. `legacy` is one of two telemetry collectors. The four regions are namer (North America), emea (Europe, Middle East and Africa), apac (Asia-Pacific) and latam (Latin America).

![Signals opened per ISO week, with the six burst weeks labelled by cause](figures/signals_per_week.png)

**The two telemetry detectors compare each account only to its own previous week.** So a drop shared by a cohort fires on every account in it. Their signals cluster on Sundays: 427 of 629 dossiers opened on a Sunday. The dossier timestamps do not establish the schedule of the other six detectors, which read text, billing and survey events.

**Weeks 21 and 24 are dominated by pipeline artefacts.** M6 marks 43 of week 21's claims and 72 of week 24's as reproducing only on uncorrected data. Real declines exist in both weeks, 3 and 4 claims, and those signals are ranked on their merits in section 2.

- **Week 21 is the May 18 pipeline migration.** Before that date the `legacy` collector recorded `api_calls` at twice their true value. The fix halved the recorded series while real traffic did not move, and the May 24 sweep compared a corrected week against an inflated one. 37 of the 43 artefact claims cite the double count in M6's explanation. The rest fail on other data problems.
- **Week 24 is the June 11–13 ingest incident.** Rows for apac and emea accounts are absent for three days. A detector that reads a missing day as zero usage sees a collapse. 64 of the 72 artefact claims cite the ingest gap.

**Weeks 14, 18, 22 and 27 are holidays, and three lines of evidence agree.** The table below compares each region's median `dau_seats` on the holiday with the same weekday one week earlier. The telemetry is deduplicated by account and date, keeping the latest `ingested_at`, and rows whose `ingest_status` is not ok are excluded.

| holiday | namer | emea | latam |
|---|---|---|---|
| Good Friday, Apr 3 | 38.9 → 7.6 | 65.1 → 14.7 | 36.7 → 8.0 |
| Easter Monday, Apr 6 | 39.9 → 40.9 | 65.2 → 15.7 | 38.3 → 40.7 |
| Labour Day, May 1 | 36.4 → 27.7 | 62.0 → 16.4 | 33.5 → 15.2 |
| Memorial Day, May 25 | 35.1 → 7.8 | 69.9 → 58.0 | 49.1 → 37.2 |
| Independence Day observed, Jul 3 | 29.2 → 6.9 | 61.8 → 58.8 | 25.3 → 48.5 |

- **The drops follow national calendars.** Easter Monday empties emea and leaves namer flat. Memorial Day and July 3 empty namer while emea moves 17% and 5%. Labour Day cuts emea by 74% and latam by 55% while namer falls 24%. May 25 is also a UK bank holiday, which explains the emea dip that day.
- **Health metrics did not move with activity.** The namer median error rate stayed between 0.44% and 0.55% on every holiday. The 95th-percentile query latency was flat on four of the five dates. On Labour Day it doubled, but that is the April 27 re-instrumentation step, which sits between the two dates compared. On Memorial Day it rose 15%. This pattern supports absence rather than failure. It does not prove the cause of every individual decline.
- **Customers said so in advance.** I counted artefacts that mention a holiday or being out of office, written by the affected accounts within 14 days of the sweep. Week 14: 5 of 28 accounts. Week 18: 10 of 40. Week 22: 10 of 36. Week 27: 9 of 36. One example is `art_02716`, sent on April 19 by a customer whose signal opened in week 18. It reads "team is on holiday next 2 wks, expect usage dip. flagged so nobody panics."

**Week 18 looks like a pipeline event but is not.** It contains the April 27 latency re-instrumentation. Its claims are about seats (25) and API calls (18), not latency, and M6 marks only 13 of them as pipeline artefacts. The cause is May 1 Labour Day. The agent labelled 40 of the 55 signals benign and still routed 9 of those 40.

**Two things I excluded.** I excluded apac from the regional comparisons because its weekly curve is shifted one day by UTC-day bucketing. I also withdrew an earlier claim that namer stayed low for two weeks after July 3. The namer weekday median by week from June 15 runs 29.9, 33.7, 22.2 (the holiday week), 30.0, 29.5, 35.4. The weeks after the holiday sit inside the range of the weeks before it.

## Under a per-owner budget of 5 signals per week, which signals should have been routed? Produce a ranking, not a classifier — attention is allocated, not labelled.

**The cap rarely binds, so the ranking's real job is a floor.** An owner-week is one owner in one ISO week. The corpus has 245 owner-weeks with at least one signal, 206 for the 14 CSMs and 39 for the 7 account executives. I apply the cap to both. Candidates exceed 5 in 23 owner-weeks, 22 of them in burst weeks. No owner-week holds more than 5 signals that clear the floor below. Filling every free slot would route 568 signals, and those churn or downgrade at 29%, against a corpus rate of 28%.

**Every signal goes into one of four tiers, and the tier is the first key of the ranking.**

- **Tier 1 (163 signals): the spec obliges a human, or the agent's own severity says one was needed.** The evidence holds a confirmed §8.1 trigger, such as a written cancellation, legal reference or billing dispute. Or the enrichment request timed out under §4.6, meaning the agent asked for more data and never received it. Or the agent itself scored the signal P0 or P1, its two highest severity labels, with a non-benign explanation. The first two conditions are sentences in the spec. The third is my reading of the §6.3 severity table. Section 3 gives each condition's count and source.
- **Tier 2 (26 signals): the decline is real but the spec does not oblige a route.** The claimed drop survives M6. The agent's hypothesis is neither benign nor absent. And the account's industry or region did not move by the same amount at the same time. 17 of the 26 sit in burst weeks.
- **Tier 3 (277 signals): everything else.** The drop could not be verified, or the agent's own explanation was benign, or there was no numeric claim.
- **Tier 4 (163 signals): pipeline artefacts and cohort-wide dips.** These stay in the ranking but never clear the floor.

**Within a tier the order is severity, then renewal date, then contract size.** Severity is the agent's label, P0 before P1 before P2 before P3. Renewal date is days to renewal, soonest first, with passed or unknown renewals last. Contract size is `arr_annual`, the account's yearly contract value from `accounts.jsonl`. I never use the agent's own estimate `arr_at_risk`, because the agent writes it and 59 dossiers state it impossibly or inconsistently. No weight is fitted.

**The policy routes tiers 1 and 2, at most 5 per owner-week.** That comes to 189 signals, 171 owned by CSMs and 18 by account executives. The full ranking, with every signal's owner, week, rank, tier and routing decision, is `analysis/attention_budget_ranking.csv`. The policy does not filter out the bursts. It routes 106 signals inside the six burst weeks, 33 in week 24 and 14 in week 21. Because eligible signals never exceed 5 in any owner-week, this corpus does not test the within-tier order under overload.

**65 of the 189 routed signals sit below the account's materiality floor.** Spec rule M4 says such a signal must not be routed as-is, while §8.1 and §4.6 forbid suppressing it. I route them and leave M4's re-scope to the owner. The agent routed 105 signals below the floor.

**One CSM's week shows how the tiers become a top 5.** Owner `u_011` had 8 candidates in week 24. Four are tier 1. One is a P1 usage cliff on a $1.67M account 16 days from renewal. Two are signals on a $237,500 account that both cite the same written legal reference, so one investigation would cover both. One is a billing dispute whose renewal passed 75 days earlier, still a §8.1 route. One is tier 2, a real seat decline on a $190K account that later downgraded. Three are below the line. One is a real 37% API drop on that same $190K account for which the agent gave no hypothesis. Two are pipeline artefacts on a $24K account. The policy routes the five and fills the slots exactly. The agent routed 3 of the 8 and suppressed both legal-reference signals, which §8.1 says must reach a human.

**The ordering carries information, and the agent's own ordering does too, but it drops spec obligations.** I cut four global orderings at the same 189 signals, with no owner cap, and counted later churn or downgrade among resolved outcomes. This ranking: 39% (55 of 140). The agent's severity, then confidence, then `arr_at_risk`: 40% (61 of 154). My risk score times `arr_annual`: 31%. Random: 28%. So the agent's own ordering matches mine on that measure. It fails on the spec. Its top 189 contains only 89 of the 163 tier-1 signals. The agent in fact routed 23 of the 54 signals with a written trigger and 22 of the 52 that timed out. This diagnostic does not separate the effect of routing fewer signals from the effect of which signals remain.

## How much better than the agent's actual operating point is your policy, and by what measure? State the agent's precision and recall as your baseline, and be explicit about what you counted as a signal that deserved attention and why.

**A signal deserved attention if any one of three conditions holds.** The evaluator checks them in this order and records the first that matches, so the counts sum to 163 of 629.

1. **§8.1, a confirmed mandatory-route trigger in the evidence (54 signals).** The spec names five triggers. They are intent to cancel, a legal reference, and a security incident. They also include a billing dispute exceeding 5% of annual recurring revenue (ARR), and a champion or economic-buyer departure within 90 days of renewal. My evaluator applies a stricter evidence test than the spec states. The artefact must exist, belong to this account, be quoted verbatim, and be written by a customer. A local zero-shot text model (DeBERTa-v3) must also read the current text, not quoted history, as that trigger. By kind: 27 billing disputes, 12 legal references, 9 departures, 3 cancellations, 3 with more than one trigger. No security incident fires alone.
2. **§4.6, the enrichment request timed out (52 signals).** The spec says a signal that timed out waiting for data must still reach a human.
3. **§6.3, the agent scored the signal P0 or P1 and committed to a non-benign hypothesis (57 signals).** The spec defines P0 as "act today" and P1 as "material risk with corroboration." So this condition reads the agent's own label as its record that a human was needed. A champion-departure hypothesis must also sit within the 90-day renewal window. This is an inference from the severity table, not a quoted obligation.

**The definition was tested against the annotators before use, and the agent's ARR estimate was rejected.** I split the 130 three-way-annotated dossiers into a development set of 71 and a held-out set of 59 before testing any candidate. Against the annotators' majority vote, the two quoted obligations alone score F1 0.19. Adding the §6.3 condition raises it to 0.38, at a permutation-test p of 0.026. All 18 hand-derived spec verdicts still pass. On the held-out set alignment is 0.29 on 10 positives, so about ten points of uncertainty. The strongest single predictor was the agent's own `arr_at_risk`, and I rejected it. A self-reported number must not be able to make a spec-required route look undeserved. Because annotator agreement helped choose the definition, the annotator comparison below includes development data and is not an independent validation.

**Outcomes corroborate the definition without having built it.** Tier 1 signals churn or downgrade at 39% among resolved outcomes. The rest churn at 24%. Tier 2 signals are routed but not counted as deserved, because the spec does not require them.

**The agent's baseline is precision 31% and recall 56%.** Its 294 routed signals contain 91 deserved ones. Seven of every ten signals it put in front of a CSM met no deserved condition. It failed to route 72 of the 163 that did. All 72 were suppressed (60) or expired (12). Among them 31 carried a written §8.1 trigger, 30 had timed out, and 11 were the agent's own P0/P1 calls. Including the §6.3 condition favours the agent, because 46 of those 57 signals are its own routes. On the two quoted obligations alone the agent scores precision 15% and recall 42%.

**My policy scores precision 86% and recall 100% on that yardstick, by construction.** The rule that defines deserved is the rule that fills tier 1. The 14 points of precision given up are the 26 tier 2 signals. These numbers prove consistency, not correctness.

**The independent measures are what happened to the accounts afterwards, with one selection effect to declare.** The complaint and wasted-escalation fields are only filled when a human saw the signal. 81 of the policy's 189 signals never reached a human, so those two fields are unobservable for them. The fair comparison is the 108 signals both policies route. Churn is observable for every resolved signal.

| measure | agent, 294 routed | policy, 189 routed |
|---|---|---|
| routed signals that later churned or downgraded | 30% (68 of 225) | 39% (55 of 140) |
| customer complaints, on the 108 signals both route | 5.4% overall (16 of 294) | 3.7% (4 of 108) |
| escalations owners marked wasted, same basis | 39% overall (115 of 294) | 27% (29 of 108) |
| accounts with realised loss reached by at least one routed signal | 97% of $11.66M | 74% of $11.66M |

The 186 agent-routed signals the policy drops carry the agent's worst outcomes: 6.5% complaints and 46% wasted escalations. These are retrospective comparisons on one corpus. They show the policy selected signals that more often had a recorded bad outcome. They do not show that deploying it would have changed any outcome. And they do not predict owner feedback on the 81 signals the agent never showed anyone.

**Against the three annotators, the policy is more precise and recalls less.** On the 130 signals all three labelled, the policy has precision 0.38 and recall 0.39. The agent has 0.31 and 0.61. Annotator approval correlates with the agent's severity label: 43% for P1 signals and 6% for P2. My definition also reads that label for 57 signals. So the two disagree mainly on signals with no spec trigger and no P0/P1 label. These rates do not establish what drove the annotators' judgments.

**The ranking cannot recover triggers that never became dossiers.** 48 written mandatory-route triggers in the artefact corpus have no dossier on the account within 14 days. They are invisible to the agent, to my policy, and to this recall figure.

**The deserved label also depends on which evidence the agent attached.** A trigger the agent never attached cannot qualify through §8.1, although the timeout and severity conditions still can. 6 of the 27 such dossiers qualify that way. The agent left 44% of those 27 unrouted against 53% for the corpus. That rate does not measure the bias. Measuring it would need corrected labels cross-tabulated against routing decisions.

## What does your policy cost in missed risk, and how confident are you in that number given what `attribution` does and does not tell you?

**I count each account's loss once, at its worst recorded outcome.** `outcomes.jsonl` is a per-signal file, and 92 of 176 accounts carry contradictory renewal outcomes across their signals. Summing per signal would count one account's loss several times.

**The policy leaves $3.02M of realised loss unreached.** 16 accounts churned or downgraded with no signal clearing the floor. Total worst-recorded loss across 62 accounts is $11.66M, so the policy reaches 74% of it. The agent reached 14 of those 16 accounts ($2.93M) and lost them anyway. The reverse, accounts the policy reaches that the agent did not, is 4 ($0.23M). Two accounts ($0.09M) were reached by neither.

**`attribution` does not establish whether routing changed these losses.** The field records whether the outcome is linked to this signal: certain, uncertain, or none, and it is null on the 158 pending outcomes. On the 26 missed signals it is uncertain on 17 and none on 9, certain on none. 17 had at least one owner play running alongside, most often a discount (9) or an executive call (5). Owners marked 7 of the 11 the agent routed as useful and still lost the account. The $3.02M measures worst-recorded loss on unreached accounts. It does not bound the policy's total counterfactual cost, and the data cannot estimate how much of it routing could have prevented.

**The policy also drops signals the record credits with a save.** 19 renewal records across 18 accounts carry certain attribution, which records a claimed contribution by the signal rather than a proven counterfactual. 4 of those accounts, $1.69M of contract value, get no routed signal under my policy. Historical loss totals do not capture this exposure.

**Confidence is high that routed volume falls and low that net outcomes improve.** The reduction from 294 to 189 and the higher churn rate among routed signals are direct counts. Whether the policy would improve customer outcomes or recoverable revenue is not measurable from this corpus. Attribution is uncertain for most outcomes where anything happened. And owner feedback is unobservable for the signals the agent never showed anyone.
