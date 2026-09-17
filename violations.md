# Violation report

**What this report is.** Cartogram runs an AI agent that watches customer accounts and decides which ones a human should look at. A specification (the spec) defines how that agent must behave. I built an evaluator that checks every one of the agent's 629 case files, called dossiers, against 29 rules taken from the spec. This report says which rules the agent breaks, which of those breaks cost Cartogram something, and where the breaks concentrate.

**Almost every dossier breaks the spec somewhere, but only one rule clearly costs money.** 621 of 629 dossiers break at least one rule, and 306 break a rule the spec classes as critical. One rule, §8.2, sits behind most customer complaints. The most frequent rule, Q2, shows no link to any bad outcome. The second most frequent, M6, marks real declines the agent exaggerated, and those accounts churn most.

**Terms used throughout.** A *signal* is one alert the agent raised about one account. Its *dossier* is the agent's record of what it did. *Routed* means the agent sent the signal to a human, recorded as disposition routed or acknowledged. 294 of 629 were routed. A *CSM* is a customer success manager, the human who investigates. A *customer-visible play* is an action the customer would notice, such as an executive escalation. *Churn* means the account left at renewal, *downgrade* means it paid less. A *wasted escalation* is one the CSM later marked as not worth the time. A *complaint* is a customer objecting to being contacted. Rule ids are the spec's own: §8.2 is section 8 rule 2, M6 is the sixth materiality rule, Q2 the second quality rule, I6 the sixth invariant.

**How the numbers are computed.** Every number here is printed by `analysis/violations_stats.py`, except the detector precision figures, which come from `analysis/detector_coverage.py`. Each rule is checked from the dossier plus the files it needs. `artifacts.jsonl` shows whether quoted evidence is real. Corrected `telemetry.jsonl` tests numeric claims. `accounts.jsonl` and `owners.jsonl` supply account flags and the owner's time zone. The other dossiers reveal duplicates. Outcomes come from `outcomes.jsonl`. Churn rates exclude the 158 signals whose renewal is still pending, so their denominator is 471. Complaint and wasted-escalation fields exist only for routed signals, so those rates are always computed among routed signals.

## What types of spec violations are most common

The table lists the ten rules the agent breaks most often. "Share" is the share of all 629 dossiers with at least one finding under that rule.

| rule | what the rule requires, in plain words | class | dossiers | share |
|---|---|---|---|---|
| Q2 | the hypothesis must match what the evidence shows | soft | 504 | 80% |
| M6 | a claimed percentage change must reproduce on corrected telemetry within 5 points | high | 259 | 41% |
| §6.3 | the first notification must arrive within the severity's target time | high | 165 | 26% |
| §5 | an action may only run on the lifecycle transition the spec allows for it | high | 125 | 20% |
| §4.8 | a lifecycle step must be an edge in the spec's transition table | high | 98 | 16% |
| M4 | a signal below the account's materiality floor must be re-scoped or suppressed, not routed | high | 95 | 15% |
| §8.5 | high confidence needs at least two distinct evidence sources | critical | 94 | 15% |
| I6 | attached evidence must exist, belong to this account, and be quoted verbatim | critical | 93 | 15% |
| Q4 | do not attach the same artefact twice, re-request data already received, or drop gathered evidence | soft | 88 | 14% |
| §8.1 | a written cancellation, legal, security or billing trigger must reach a human | critical | 62 | 10% |

**The agent's most frequent failure is reasoning, not conduct.** Q2 fires when the agent commits to an explanation that nothing verified supports. In 293 findings the explanation is "benign variation," meaning a normal fluctuation, with no evidence that the account's peers moved the same way. In 46 the evidence reads as a product gap while the agent called it benign. Q2 is a soft rule. The spec says quality rules are "expectations for a good dossier," not conduct rules.

**The second failure is quoting numbers that the corrected data does not show.** M6 fires on 259 dossiers. In 197 the claimed change reproduces on raw telemetry before deduplication, before the legacy double-count fix, or before the June ingest-gap exclusion. In 60 it reproduces on neither raw nor corrected data. The telemetry corrections are described in `attention_budget.md`, section 1.

**Ten critical rules fire, and seven of them are about evidence and containment.** The three largest are §8.5 high confidence on one source (94), I6 evidence that is missing, misquoted or from another account (93), and §8.1 a mandatory route the agent suppressed (62). The next four are containment rules. §8.2 is a customer-visible play on a restricted account (41). §8.3 is a restricted artefact quoted in a routed dossier (22). §8.4 is another account's artefact attached (19). §8.7 is contact details carried into a quote (18). The other three are lifecycle invariants: I1 backward transition (31), I2 action after an exit state (25), I5 more than one hypothesis (10). Three rules never fire: I3, M2 and Q1.

**Two of the critical rules are one behaviour.** I6 and §8.7 fire together on 17 dossiers, 6.4 times more often than chance. In all 17 the quote is verbatim up to an appended email address. In 16 of the 17 that address appears nowhere in the source artefact. The agent invents a contact detail and breaks the verbatim rule in one move.

## Which violations correlate with bad outcomes

**Most complaints follow one rule.** A complaint can only happen after a customer-visible play that reached a human. There are 208 such dossiers and 16 complaints. The table compares dossiers that broke §8.2 with the rest, on that denominator.

| among the 208 routed dossiers with a customer-visible play | dossiers | complaints | complaint rate |
|---|---|---|---|
| broke §8.2 (play on an account under legal hold or an M&A quiet period) | 31 | 10 | 32% |
| did not break §8.2 | 177 | 6 | 3% |

![10 of 16 complaints followed a customer-visible play on a legal-hold or M&A account](figures/complaints.png)

The domain guide predicts this. It says reaching out during a quiet period is "the most reliable way to generate a complaint." No other rule moves the complaint rate on this denominator: late notification (§6.3) is 8% with and 8% without, routing below the floor (M4) is 7% against 8%. The other six complaints carry no rule that separates them from routed dossiers without a complaint.

**No rule moves the wasted-escalation rate.** Among the 294 routed dossiers, 39% were later marked wasted. The rules I expected to matter do not. Off-hours paging (§6.1) is 44% against 38%. Routing below the floor (M4) is 41% against 38%. Wrong channel (§8.6) is 47% against 39%. A restricted quote (§8.3) is 40% against 39%. The one rule with a gap runs the other way. Dossiers with a late notification (§6.3) are wasted at 32% against 46% for on-time ones, because late signals are more often P1, and P1 signals are more often real. A first draft of this report showed these rules doubling the wasted rate. That was a denominator error: the rules fire almost only on routed dossiers, so comparing against the whole corpus compared routed against unrouted.

**Churn is associated with severity, not with any rule.** The strongest raw association is late notification, §6.3, at 38% churn against 24%. It is confounded. 102 of the 165 late dossiers are P1, the severity with the tightest 24-hour target. P1 signals churn at 39% against 21% for P2 whether they are late or not. Within P1, late signals churn at 42% against 28% on 18 on-time dossiers, which is inside noise. Being late does not cause churn. No rule predicts churn well enough to use as a warning.

**Two rules show zero complaints by construction.** §8.1 and I2 fire only on dossiers that were suppressed or expired. No human saw them, so no complaint or wasted escalation could be recorded. The harm of a suppressed cancellation notice is invisible in every outcome field. §8.1's 62 dossiers churned or downgraded at 31%.

## Which violations are common but harmless, and how I established that

**The test.** A rule is harmless in outcome terms if, among routed dossiers of one detector, the churn and wasted rates with the rule match the rates without it. Restricting to routed dossiers removes the denominator error above. Restricting to one detector removes the detector's own base rate, because the two telemetry detectors carry most of Q2 and all of M6. The limit of the test: the "without" groups hold 13 to 19 dossiers, so it can only detect differences above about 25 points.

| rule | detector | routed with the rule: n, churn, wasted | routed without: n, churn, wasted |
|---|---|---|---|
| Q2 | usage_cliff | 79, 32%, 44% | 18, 31%, 50% |
| Q2 | seat_decay | 69, 40%, 35% | 19, 27%, 42% |
| M6 | usage_cliff | 60, 37%, 47% | 37, 22%, 43% |
| M6 | seat_decay | 47, 42%, 32% | 41, 30%, 41% |

**Q2 is common and harmless on every check I can run.** Within detector, neither churn nor wasted rates separate beyond the test's resolution. Among the 335 unrouted dossiers, where a wrong "benign" call could cause a missed churn, Q2 dossiers churn at 26% and non-Q2 at 26%. The annotators flagged a wrong hypothesis on 19% of Q2 dossiers and 22% of the rest. So either Q2 is harmless or it fires too broadly to tell. The 80% rate makes the second reading live, and it is a limitation of the evaluator. Q2's text test uses an uncalibrated 0.35 threshold. Its "benign" test requires the account's region or industry to have moved with it.

**M6 is common and not harmless, but its harm is not what the spec expects.** The spec calls an ungrounded number "the most common way this system wastes attention." The wasted rate does not support that: 40% with M6 against 39% without, among routed dossiers. What M6 marks depends on what the corrected data shows, and the evaluator records which case it is.

![An ungrounded number usually hides a real decline, and those accounts churn most](figures/m6_split.png)

Of the 259 M6 findings, 121 are real declines the agent exaggerated, and routed accounts in that group churned or downgraded at 53%. 58 are declines the pipeline manufactured, with no real change underneath, and those churned at 12%. 80 are claims that reproduce on neither series, at 30%. The harm of M6 is a misstated number in front of a human, most often on an account that was genuinely in trouble.

**One check I removed.** A first draft reported that dossiers with a wrong `arr_at_risk` have lower quality scores. The quality score is computed from the violations, so that comparison was the evaluator agreeing with itself. The independent version is the annotators' quality score: median 0.80 on the 59 dossiers with a wrong `arr_at_risk`, 0.86 on the rest.

## Violation rates by account tier, region, detector and owner

The measure is the share of a group's dossiers with at least one critical finding, and the mean number of findings per dossier. "Top rules" lists the two most frequent rules in the group after Q2. Q2 leads everywhere.

| group | n | critical | findings per dossier | routed | top rules after Q2 |
|---|---|---|---|---|---|
| tier mid_market | 292 | 51% | 3.2 | 46% | M6 42%, §6.3 28% |
| tier growth | 201 | 47% | 3.0 | 48% | M6 39%, §6.3 26% |
| tier enterprise | 136 | 46% | 2.9 | 46% | M6 42%, §6.3 23% |
| region namer | 268 | 50% | 2.9 | 42% | M6 32%, §6.3 21% |
| region emea | 223 | 51% | 3.3 | 48% | M6 49%, §6.3 28% |
| region apac | 110 | 42% | 3.2 | 56% | M6 48%, §6.3 33% |
| region latam | 28 | 46% | 3.1 | 50% | §6.3 36%, M6 36% |
| detector usage_cliff | 205 | 47% | 3.3 | 47% | M6 67%, §6.3 25% |
| detector seat_decay | 190 | 46% | 3.3 | 46% | M6 64%, §5 23% |
| detector exec_churn_language | 143 | 49% | 2.7 | 43% | §6.3 31%, §5 20% |
| detector billing_dispute | 26 | 58% | 2.7 | 31% | §8.1 50%, §5 23% |
| detector security_review_opened | 12 | 92% | 3.8 | 67% | §8.3 67%, Q4 50% |
| detector sentiment_drop | 18 | 67% | 2.6 | 44% | §6.3 28%, §8.5 22% |

**Tier and region are flat.** Every tier sits between 46% and 51% critical, and the three large regions between 42% and 51%. Emea and apac carry more M6 for two reasons. The June 11–13 ingest gap dropped their rows, which explains 47 of emea's 109 M6 findings and 20 of apac's 53. And more of their signals come from the two telemetry detectors, where M6 lives.

**Detector is where the rates differ.** M6 exists only on the two telemetry detectors, at 67% and 64% of their dossiers, because only those detectors make numeric claims. Billing dispute signals carry a suppressed mandatory route half the time. Security review signals are 92% critical (11 of 12), mostly because the agent quoted the restricted security artefact in 8 of them (§8.3). That is the containment rule the spec attaches to those artefacts. The spec asks whether the detectors are high-precision. Measured on corrected telemetry, precision is 41% for usage_cliff and 51% for seat_decay. The text detector exec_churn_language is about 9%, because it fires on quoted history.

**Owner spread is wide but not significant.** Across the 14 CSMs the share of dossiers with a critical finding runs from 33% (u_004, 24 dossiers) to 66% (u_010, 41 dossiers). A chi-square test across the 14 gives 20.9 on 13 degrees of freedom, below the 22.4 needed at p = 0.05. The spread is within chance for groups this small. Where owners do differ, the book explains it. Owners are assigned per account. The share of an owner's accounts under legal hold or a quiet period runs from 0% to 40%. The three owners with the most §8.2 findings (9, 8, 8) hold 31%, 40% and 26% restricted accounts. The seven account executives hold 52 dossiers between them and are too few to read.

## Specific dossier examples with evidence

- **§8.2, sig_0019, step 6, acct_0014.** The agent recommended a `csm_checkin`, a customer-visible play, on an account flagged legal_hold. The customer complained, the owner marked the escalation wasted, and the account downgraded. It also carries an M6 finding at step 2: a claimed 46% seat drop that is 35% on corrected data.
- **§8.1, sig_0148, step 6, acct_0109.** A billing_dispute signal with a confirmed billing-dispute trigger in its evidence was suppressed with no human notified. The account churned.
- **I6, sig_0001, step 2, art_00620.** The dossier quotes "We are planning a backfill of about 180M rows." The artefact says 90M. The same dossier scores at step 3 by riding a transition the action table forbids (§5), and its hypothesis rests only on artefact claims (Q2). The account churned and the owner marked the escalation wasted.
- **§8.4, sig_0062, step 2, art_03153.** The attached artefact belongs to acct_0150. The signal is on acct_0175. The dossier was routed, and the account downgraded.
- **M5, sig_0483, step 5, acct_0134.** `arr_at_risk` of $6,500 is later stated as $78,000, a twelve-fold restatement, which the spec names as monthly-versus-annual confusion. The same dossier was routed with `arr_at_risk` below the floor (M4) and high confidence on one source (§8.5). The account downgraded.
- **§6.3, sig_0281, step 8, acct_0013.** First notification 417 hours after open against a 72-hour P2 target. The dossier also fabricates a quote by appending "sarah@solaris.com" to a status message (I6 and §8.7), and it was routed with `arr_at_risk` of $3,000 against a $6,000 floor (M4).
- **§8.3, sig_0118, step 2, art_03567.** A restricted artefact quoted verbatim in a dossier that was routed and acknowledged. The owner marked the escalation wasted.

**What the examples share.** The dossiers with the worst outcomes are not the ones with the most findings. They are the ones where one containment or routing rule broke: a quiet-period outreach, a suppressed dispute, a restricted quote. The frequent rules, Q2 and M6, are present in almost all of them and distinguish nothing.

## Beyond the five questions: what else the analysis showed

**The agent invents contact details.** The 17 dossiers where a quote is verbatim except for an appended email address, and the address is not in the artefact, are one behaviour, not two rule breaks. It is the cleanest fabrication pattern in the corpus and the easiest to fix at the source.

**The agent's numbers are wrong in a specific, checkable way.** 59 dossiers state an `arr_at_risk` that is impossible (above the contract) or restated inconsistently. The ranking in `attention_budget.md` never reads that field for this reason.

**Half the agent's alerts that a human saw were not worth the slot, and no rule explains which.** 115 of 294 routed signals were marked wasted, and no violation rule separates the wasted ones from the rest. The waste comes from routing signals with no spec obligation at all, which the budget document treats as a selection problem rather than a conduct problem.

**The evaluator has limits that shape every number above.** Q2 uses an uncalibrated text threshold and fires on 80% of dossiers. The quality score is built from the violations and cannot be used as independent evidence. Complaint and wasted fields exist only for routed signals, so no rule that fires on suppressed signals can ever show harm in them. And 48 written mandatory triggers never became a dossier, so the evaluator never saw them.
