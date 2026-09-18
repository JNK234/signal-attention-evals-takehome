# Evaluating Cartogram's attention agent against its specification

Narasimha Jwalapuram. Signal Labs AI-engineer take-home, September 2026.

## Setup

**What Signal Labs builds.** Signal Labs builds the layer that decides which few things a human must look at today. At Cartogram, the fictional customer in this assignment, that layer is an account-risk attention agent.

**What the agent does.** The agent watches 180 customer accounts. It reads product telemetry: daily metrics such as active seats and API calls. It reads text artefacts: tickets, email, chat, CRM notes, meeting notes, surveys, security reviews, billing events and monitoring alerts. When one of eight detectors fires, the agent opens a signal. It then:

1. Gathers evidence.
2. Forms a hypothesis about the cause.
3. Assigns a severity and a confidence.
4. Routes the signal to the account owner, a customer success manager (CSM), or suppresses it.

A dossier is the full record of one signal: each state, each attached artefact with its quote, each numeric claim, each action and notification, and the final decision.

**What we were given.**

- A specification. It defines the agent as a state machine over the life of a signal. It adds invariants, timing targets, policy and containment rules, materiality rules, grounding rules for numeric claims, and quality expectations.
- 629 dossiers from a 21-week period.
- Telemetry: 32,032 account-day rows.
- 3,810 text artefacts, plus the account and owner records.
- Renewal outcomes observed after the period.
- Quality labels from three annotators. Each labelled 240 dossiers.

**What we had to build.** An evaluator that takes one dossier and returns four things: a quality score, a risk score, a verdict on whether the signal deserved human attention, and the list of rules the dossier broke. On top of it: a violation report, an attention-budget analysis, and this writeup.

**Why this is hard.** A dossier cannot be judged from its own contents:

- The agent quotes percentage changes computed on telemetry that carries pipeline distortions.
- It quotes evidence that can be missing or from another account.
- It reports its own ARR-at-risk figure.
- The outcomes name the signal as the cause in only 28 of 629 cases.
- The complaint and wasted-escalation fields exist only for signals a human saw.
- The three annotators disagreed on 31% of the dossiers they all labelled.

Every claim in a dossier must be checked against the source it cites. Every number in this writeup states the population it was measured on.

## Methodology

**The evaluator is a source-anchored audit.** It recomputes what the agent claimed from the data the agent cited. Figure 1 shows the path one dossier takes. Two things are built once: a rule table from the specification, and a truth layer from the data files. Each dossier then passes through eleven checks. The checks emit violations. Three scores are computed from the violations and from facts the checks record.

![Figure 1. The path one dossier takes through the evaluator. Grey boxes are deterministic. Amber boxes use the text model or its cache.](figures/pipeline.png)

*Figure 1. The path one dossier takes through the evaluator.*

### From the specification to 29 rules

- I read the specification once and transcribed it into a table of 29 rules.
- Each rule carries the identifier the specification uses, for example I6 (evidence integrity), M6 (grounding of numeric claims) or §8.2 (legal-hold containment).
- Each rule also carries its section, a severity class, and the check that owns it.
- The severity classes come from the specification's summary table in §11. It calls invariant violations, fabricated evidence, containment failures and missed mandatory routes critical. It calls invalid transitions and materiality or grounding errors high. It calls timing failures medium to high and quality failures variable.
- I mapped those words to four weights: critical 1.0, high 0.6, medium 0.3, soft 0.1.
- Two departures are documented in the code. §8.6 (wrong notification channel) is soft because the specification calls it low-severity. Timing is split: the time-to-attention target and the re-notification limit are high, the notification window and the staleness rule are medium.
- Nothing parses the PDF at run time. A hand-written table is easier to audit than a parser.
- Seven tests parse the section numbers of the LaTeX source. They fail in three cases: a rule cites a section that does not exist, an emitted identifier is not in the specification, or a coined name has no entry in the definitions file.

### The truth layer

The optional `load_context` call receives the accounts, owners, telemetry, artefacts and the other dossiers. It indexes the records by identifier. It corrects the telemetry in five ordered steps:

1. Where an account-day appears twice, the row with the later ingestion time wins. The later row is a backfill.
2. For accounts on the legacy collector, halve API calls before 18 May 2026. The collector double-counted until that date.
3. Multiply the p95 latency before 27 April by 1.8. The instrumentation changed on that date.
4. Mark as unusable any row where seats, API calls and data volume are all zero while the status reads ok. Mark degraded rows the same way.
5. Leave missing days missing.

Step 5 matters most. A naive reader fills a missing day with zero and sees a collapse. The evaluator refuses to compute a number over a window it cannot see.

To recompute a percentage change, the evaluator pairs each day in the claimed window with the same weekday one window earlier. It sums only the pairs where both days are usable. Weekends, missing days and the apac time-zone shift then cancel.

The truth layer also builds a cohort baseline for each account: the median change of its industry or region peers over the same days, with the account itself left out. A drop the whole cohort shares then reads as a calendar or pipeline event.

### Reading the text

Artefacts are messy. Quoted email history repeats old complaints under a new positive message. Signatures carry contact details. Sarcasm defuses complaints. The reader has four parts.

**1. Quote-depth splitter.** The evaluator splits each artefact at quote boundaries. Text under a "wrote:" line, below a forwarded-message divider, or prefixed with ">" is tagged as quoted history. Quoted history can never fire a trigger. The splitter is a set of regular expressions over the conventions this corpus uses. A convention it does not list reads as current text. Trailing signatures are tagged the same way.

**2. Zero-shot NLI model.** A natural-language-inference (NLI) model takes a passage and a written sentence. It returns how strongly the passage entails the sentence. Zero-shot means the model was never trained on this corpus. I chose DeBERTa-v3-base fine-tuned for zero-shot classification (184 million parameters, runs offline) from a bake-off of three candidates on hand-written fixtures. It separated 11 of 12 labels with bimodal scores. It read German, Spanish, Portuguese and Japanese cancellation notices correctly. A multilingual NLI model scored almost everything near 1.0 and separated 7 of 12.

**3. Labels and thresholds.** The model scores twelve labels: the five mandatory-route triggers from §8.1 (cancellation intent, legal reference, security incident, champion departure, billing dispute), sarcasm, and six hypothesis topics from §3.2. Each label has several written sentences and takes the highest score, so recall does not depend on one phrasing. A score becomes True above an upper threshold, False below a lower one, and abstain between them. I calibrated the thresholds on one half of 176 fixtures and checked them on the other half. The model got 64 of 68 right, with 2 abstentions.

**4. Structural gates.** Four gates sit in front of every model verdict:

- The artefact must exist and belong to the account.
- The quote must appear verbatim.
- The block must be current, not quoted history.
- A cancellation or security trigger must come from a customer author.

The 43,679 scores for this corpus are committed as a cache. The grader's machine reproduces every result with no model download. The model sits behind a four-line interface, so any scorer can replace it. With neither model nor cache, the evaluator marks the four text-dependent rules as unevaluated and returns everything else.

### The eleven checks

The evaluator first coerces the dossier into the expected shape and records what it changed. A malformed field can never hide findings on valid fields. Nothing in `evaluate()` raises. The checks then run in this order:

| check | rules | needs |
|---|---|---|
| lifecycle (a finite-state acceptor over the transitions) | I1, I2, I3, §4.8 | dossier |
| actions against their allowed transitions | §5 | dossier |
| hypothesis count | I5 | dossier |
| timing, in the owner's local time zone | §6.1 to §6.4, §8.6 | owner record |
| materiality arithmetic | M1 to M5 | account record |
| evidence integrity | I6, §8.4, Q4 (staleness) | artefacts, model |
| policy and containment | §8.2, §8.3, §8.5, §8.7 | account record |
| grounding on corrected telemetry | M6 | telemetry |
| duplicates across the other dossiers | Q5 | other dossiers |
| mandatory route | §8.1 | artefacts, model |
| hypothesis quality | Q1 to Q4 | telemetry, model |

Eight checks are pure logic. Three consume model verdicts: evidence staleness, the mandatory route, and the hypothesis-fit rule Q2.

Each violation is a record of four fields: the lifecycle step, the rule identifier, a severity, and a one-line explanation. The explanation names the offending identifiers and numbers. Example: "api_calls claimed −61% reproduces only on uncorrected data (raw −58.2%, paired +1.3% on 7 of 7 pairs), manufactured decline". Severity is the class weight, halved when the finding is uncertain. An uncertain finding is, for example, a trigger the model abstained on, or a departure the evaluator could not attribute to a named person. Magnitude never changes severity. It lives in the explanation.

A second entry point, `explain()`, returns the same result plus 31 recorded facts: which triggers fired and from which artefact, whether a human was reached, the status of each numeric claim, the cohort match, and more. Every analysis script reads those facts instead of re-deriving them.

### The quality score

The formula is

    quality = Π over broken rules of (1 − 0.16 · w · c)

where w is the class weight (1.0, 0.6, 0.3 or 0.1). c is 1 for a confirmed finding and 0.5 for an uncertain one. There is one penalty per rule. Five forms came before it:

1. **Weights chosen by feel.** Rejected: they traced to nothing.
2. **Zero on any critical violation.** Rejected for two reasons. No annotator ever scored a dossier at zero (their minima were 0.30, 0.27 and 0.88). And it merges two axes the specification keeps apart: quality is how well the dossier was built, risk is how much harm it can cause.
3. **A floor of 0.25.** Rejected: no source.
4. **A sum of penalties, clipped at zero.** The annotators' own behaviour suggested it. Regressing each annotator's quality score on the severities they recorded gives a straight line. The slopes are −0.161, −0.162 and −0.145. The R² values are 0.60, 0.82 and 0.70. But the clipped sum pinned 134 of 629 dossiers (21%) at exactly 0.0, two of them with no critical violation. Terwee and colleagues (2007) call more than 15% of scores at the lowest value a floor effect: those dossiers cannot be told apart.
5. **The product.** It removes the floor (one dossier remains at it). It keeps the rank order: the Spearman correlation with each annotator moved by less than 0.015. It is the same algebra the Common Vulnerability Scoring System (CVSS) uses for its impact sub-score.

Last, I re-anchored the magnitude. An earlier table charged 0.50 per critical finding, three times the annotators' slope, and sat every score 0.29 below every annotator. Setting the slope to 0.16 and keeping the specification's 10:6:3:1 ratios moved the evaluator's median quality from 0.54 to 0.80. The annotator medians are 0.80, 0.83 and 0.96. The three annotators agree on the cost of a finding to within 0.017 and disagree on their baseline by 0.15. That is why only the slope is borrowed.

### The deserved-attention verdict

The specification never says positively what deserves attention. It states two obligations. §8.1 lists five triggers that must reach a human. §4.6 says a signal whose enrichment timed out must still reach a human. The verdict went through three versions:

1. **Eight conditions, one from the specification.** It fired on 215 dossiers, including a 25-day-old feature request. Rejected.
2. **The two obligations only.** It fired on 106. Against the three annotators its Cohen's kappa was 0.04, 0.10 and 0.02, all with confidence intervals crossing zero. Kappa is agreement beyond what chance predicts from each rater's yes-rate, so those values mean no agreement. Trivial baselines beat it: calling every P0 or P1 signal deserving scored an F1 of 0.57 against the annotators' majority vote, and this version scored 0.19.
3. **The two obligations plus one inference from §6.3.** This is the shipped version, with 163 positives.

How version 3 was reached:

- Before testing any candidate, I split the 130 dossiers all three annotators labelled into two halves by hashing the signal identifier: 71 development and 59 held-out. No candidate could be tuned on the held-out half. One candidate scored 0.39 on development and 0.09 on held-out. The split caught it.
- I swept 106 candidate policies built from 14 atomic predicates. Each was tested against the majority vote with a permutation test. A permutation test shuffles the labels 3,000 times to see how often chance alone reaches the same score. Each survivor had to keep all 18 verdicts I had derived by hand from the specification before the evaluator ran.
- Every evidence-only predicate was noise (p between 0.15 and 0.42). The only real signal was the agent's own severity.
- §6.3 gives that severity meaning: P0 means act today, P1 means material risk with corroboration. So the third row is: the agent assigned P0 or P1 with a non-benign hypothesis, and a departure claim sits inside the 90-day renewal window.
- The row raised the F1 against the majority from 0.19 to 0.38 (permutation p = 0.026) and kept 18 of 18 hand-derived verdicts. The code marks it as an inference. A test asserts the marking.
- One predictor I refused on principle. The agent's own ARR-at-risk figure was the strongest single signal on held-out data. It is wrong on 59 dossiers. An evaluator must never let a self-reported number veto a route the specification requires.

### The risk score

A noisy-OR combines independent causes of one outcome. If condition i causes harm with probability p_i, the probability that at least one does is

    risk = 1 − Π over fired conditions of (1 − p_i · c_i)

with c_i the same certainty factor as above. The ten conditions are:

- Six containment and integrity rules: §8.2, §8.3, §8.4, §8.7, I6, §8.1. Probability 0.45.
- Two materiality rules: M4, M6. Probability 0.20.
- Two judgments from the domain guide: a customer-visible play on an undeserving signal, and a deserving signal that never reached a human. Probability 0.15.

How the form and the magnitudes were reached:

- The first version had seven additive coefficients that cited nothing.
- I rejected fitting coefficients to outcomes. Sixteen complaint events cannot support a weight per condition. An evaluator that runs on dossiers nobody has seen must not encode one sample's accidents.
- I rejected a sum. Fifteen of the 16 complaints fire two conditions at once, so a sum charges one event twice.
- The 0.45-to-0.20 step borrows the ratio between CVSS's high and low impact levels (2.55×).
- The judgment tier sits below the high tier. GRADE, the evidence-grading framework, never lets inferred evidence outrank rated evidence.
- A weight-share audit showed the wasted-CSM-slot harm held 12% of the score's mass, while the annotators raised it in 53% of their flags. I swept four materiality candidates on a second pre-registered split and added M4 (routing below the materiality floor). It was the only one with an effect.
- Two conditions that describe one event, a suppressed mandatory trigger and a deserving signal that reached no human, are grouped and charged once.

### Validation design

Three ground truths are used, and every number in this writeup says which one it rests on:

- The annotators' majority vote, for agreement statistics.
- Twenty-seven golden dossiers, for correctness. I derived their expected violations and verdicts by hand from the specification and the raw records before the evaluator ran on them.
- The renewal outcomes, for harm, with the caveat from Setup.

Two pre-registered development/held-out splits guard the deserved and risk definitions. "Not fitted to this corpus" covers the magnitudes. Which conditions exist in the risk score was chosen partly by held-out performance. That is model selection, and Limitations records it. 751 tests cover the checks, the scores, the cold path and the provenance of every constant.

## The data

### Six distortions in the telemetry

The file holds 32,032 rows for 31,698 distinct account-days over 181 days. 334 account-days appear twice. 882 are absent. 72 rows are flagged degraded. 48 rows across 7 accounts show zero seats, zero API calls and zero data volume while the status reads ok.

The domain guide documents three events:

- The legacy collector double-counted API calls until 18 May 2026.
- The p95 latency metric changed scale by about 1.8× on 27 April.
- The apac and emea rows for 11 to 13 June never arrived.

Three more came only from inspection:

- The duplicate rows are backfills. The later version is the correction.
- The zero rows marked ok are collector outages. An account that looks dead is one whose meter stopped.
- The apac daily active users are bucketed by UTC day. This shifts that region's weekly curve by one day.

Each of these makes a healthy account look as if it collapsed, on the same date, across a whole cohort.

### What the correction changed

The rule was refuse, never zero. Methodology gives the five steps. The effect: 259 of the 629 dossiers state a percentage change that does not reproduce on corrected telemetry. 197 of those reproduce on the raw file. The 259 split three ways by what the corrected data shows:

- 121: a real decline existed and the agent overstated it.
- 80: the claim matches neither series.
- 58: the pipeline manufactured the decline.

The three groups behave differently later. That is the M6 finding in Findings.

### Why the load is bursty

Signals per week have a median of 21 and a maximum of 107. 427 of 629 signals opened on a Sunday, because the detectors sweep whole cohorts on that day. Six weeks hold 40 or more signals and together 58% of the corpus (Figure 2). Each burst has a named cause:

- Week 21 is the 18 May collector migration. 37 of its 43 artefact-status claims cite the legacy metric.
- Week 24 is the June ingest gap. 64 of its 72 cite the missing days. 86 of its 107 accounts sit in apac or emea.
- Weeks 14, 18, 22 and 27 are public holidays: Good Friday, the 1 May Labour Day, Memorial Day and 3 July. On Memorial Day the median North American account fell from 35.1 active users the week before to 7.8. Its error rate held at 0.44 to 0.46. On each holiday week, 5 to 10 affected accounts had an out-of-office or holiday artefact within 14 days.

A first draft claimed the Memorial Day drop persisted into July. The same-weekday medians for the next six weeks sit inside the pre-holiday range, so I withdrew that claim. `attention_budget.md` section 1 carries the full table.

![Figure 2. Signals per week with the cause of each burst week.](figures/signals_per_week.png)

*Figure 2. Signals per week, with the cause of each burst week.*

### The detectors are low-recall

I re-ran the two threshold detectors on corrected telemetry:

- usage_cliff (a week-over-week API-call drop above 34%) fired for 92 of 322 real episodes: coverage 29%. 84 of its 205 dossiers match a real episode: precision 41%.
- seat_decay (a seat drop above 29%) reached coverage 23% and precision 51%.
- On raw telemetry the same thresholds would have seen 517 and 596 episodes. About 195 and 140 of the episodes the detectors reacted to were manufactured by the pipeline.
- exec_churn_language carries no current trigger in 125 of its 143 dossiers.
- 48 written mandatory-route triggers have no dossier within 14 days: 23 departures, 14 billing disputes, 5 cancellations, 5 legal references, 1 security incident.

The specification calls the detectors "deliberately high-recall". The corrected data shows 23% to 29%.

### Two text traps that mattered

- Quoted email history. Three dossiers fire an executive-churn reading only on text under an "On 02 Mar 2026 … wrote:" line. The quote-depth splitter stops that.
- Appended contact details. 17 dossiers quote an artefact with an email address appended. The address is absent from the artefact in 16 of them. That is both a fabricated quote (I6) and a contact-detail leak (§8.7).

## Annotator disagreement

### The four statistics

- **Cohen's kappa** is agreement between two raters after subtracting the agreement chance alone would produce from their yes-rates. 0 is chance, 1 is perfect.
- **PABAK** (prevalence-adjusted bias-adjusted kappa) is two times the raw agreement minus one. When both raters say "no" about 80% of the time, as here, kappa drops even when they agree often. PABAK ignores the base rate, so I report it beside kappa.
- **Spearman's rho** is the correlation between two raters' rankings of the dossiers. It measures order and ignores level.
- **Krippendorff's alpha** is chance-corrected agreement for any number of raters. On a 0-to-1 score it can treat the score two ways. Interval: 0.62 versus 0.63 is a small disagreement. Nominal: any difference is total disagreement.

`analysis/annotator_agreement.py` prints every number in this section.

### How much the annotators agree

On the 130 dossiers all three labelled:

- Deserved attention: pairwise kappa 0.44, 0.43 and 0.47, with bootstrap 95% intervals from about 0.23 to 0.63. PABAK 0.59, 0.59 and 0.57. Raw agreement 79%.
- Quality score: pairwise Spearman 0.43, 0.31 and 0.45. Krippendorff's alpha 0.186 as interval and −0.04 as nominal.

The nominal alpha means no agreement at all for anybody who reads the score as a grade. The interval alpha means the annotators disagree by small distances.

### Each annotator is consistent about something different

- Annotator 1 reads for wasted attention. 62% of their failure flags are that category.
- Annotator 2 reads for policy and evidence. 19% of their flags are evidence integrity and 19% policy, two to three times the others. This annotator is closest to the specification.
- Annotator 3 is generous and reads for calibration. Their minimum quality score is 0.88, their median 0.96. 29% of their flags say the agent's confidence or severity was miscalibrated.

Their quality scores share one slope on severity and differ in intercept (0.84, 0.92 and 0.99). That is why Methodology borrows only the slope.

### Three blind spots in the labels

- Annotators never see the unrouted half of the corpus as a routing question. Only 6 of 606 flags say a signal was missed. The evaluator's F1 against the majority vote on dossiers that never reached a human is 0.00, because the annotators almost never say yes there.
- The wasted-attention flag and the wasted-escalation outcome field disagree. The flag covers 64% of the outcome-wasted dossiers. Only 26% of flagged dossiers are outcome-wasted, partly because annotators flag unrouted signals the field cannot see.
- The free-text assessment is a fixed pool of phrases reused across dossiers. On sig_0153 it contradicts the dossier's own disposition.

### How I used them

I used the annotators as a check, never as a training label:

- The majority vote is the reference for agreement statistics.
- The pre-registered split from Methodology stops any candidate being tuned on the held-out half.
- Every agreement number is reported per annotator and per slice.

The evaluator lands below the human band: kappa 0.12, 0.32 and 0.23 against the three annotators, with the first interval crossing zero. PABAK is 0.40, 0.47 and 0.40. Severity explains the gap. The annotators say yes to 43% of P1 signals and 6% of P2, so they track the agent's severity label. The specification ignores that label. Adding the evaluator to the panel raises quality alpha from 0.186 to 0.295. That figure is in-sample, because the slope comes from these annotators. It shows consistency with them and nothing more.

## Findings

### The evaluator against the annotators

Table 1 puts the evaluator beside the human-versus-human numbers. F1 is the harmonic mean of precision and recall against the majority vote. MAE is the mean absolute difference between two raters' quality scores.

| statistic | human vs human | evaluator vs annotators |
|---|---|---|
| deserved: kappa | 0.44 / 0.43 / 0.47 | 0.12 / 0.32 / 0.23 |
| deserved: PABAK | 0.59 / 0.59 / 0.57 | 0.40 / 0.47 / 0.40 |
| deserved: F1 vs majority | | 0.38 |
| quality: Spearman rho | 0.43 / 0.31 / 0.45 | 0.36 / 0.55 / 0.44 |
| quality: MAE | 0.11 / 0.17 / 0.12 | 0.12 |
| quality: median | 0.80 / 0.83 / 0.96 | 0.80 |
| quality: Krippendorff alpha (interval) | 0.186 | 0.295 with the evaluator added |

*Table 1. Agreement on the 130 three-way-labelled dossiers (human vs human) and on each annotator's 240 (evaluator vs annotators).*

- On quality, the evaluator sits inside the human band on every statistic.
- On deserved attention, it sits below the band. The per-slice view says where: F1 against the majority is 0.51 on P1 dossiers and 0.00 on P2, P3, suppressed and never-routed dossiers.
- Two trivial baselines beat the evaluator's majority F1 of 0.38. Calling every P0 or P1 signal deserving scores 0.57. Calling every non-benign hypothesis deserving scores 0.57. The first breaks 4 of the 18 hand-derived specification verdicts. The second is the agent grading itself. The evaluator is the only definition that clears both the annotators and the specification.

### The agent's failure is selection, not volume

621 of 629 dossiers break at least one rule. 306 break a critical one. Table 2 lists the ten most frequent.

| rule | requirement | class | dossiers |
|---|---|---|---|
| Q2 | hypothesis matches the evidence | soft | 504 (80%) |
| M6 | claimed change reproduces on corrected telemetry | high | 259 (41%) |
| §6.3 | first notification within the severity's target | high | 165 (26%) |
| §5 | action rides an allowed transition | high | 125 (20%) |
| §4.8 | lifecycle edge in the transition table | high | 98 (16%) |
| M4 | no routing below the materiality floor | high | 95 (15%) |
| §8.5 | high confidence needs two sources | critical | 94 (15%) |
| I6 | evidence exists, same account, verbatim | critical | 93 (15%) |
| Q4 | evidence hygiene | soft | 88 (14%) |
| §8.1 | mandatory trigger reaches a human | critical | 62 (10%) |

*Table 2. Rules by number of dossiers with at least one finding, of 629.*

- Against the three deserved conditions, the agent's 294 routed signals have precision 31% and recall 56%.
- Against the two quoted obligations alone: 15% and 42%.
- The 72 deserving signals it never routed were 60 suppressions and 12 expiries. 61 of them carried a written trigger or a timeout.
- Capacity never bound. Candidates exceeded the 5-per-week cap in 23 of 245 owner-weeks. The agent itself exceeded it in 2. Routing every eligible signal (568) would give a churn rate among routed of 29%, against a corpus base of 28%.

### One rule precedes complaints, and no rule explains waste

Complaints can only follow a routed customer-visible play. There are 208 such dossiers, with 16 complaints.

- Dossiers that broke §8.2 (a customer-visible play on a legal-hold or M&A-quiet-period account) complained at 32% (10 of 31). The rest complained at 3% (6 of 177). Figure 3 shows this.
- No other rule moved the complaint rate on that denominator.
- Two failures of similar frequency carried costs an order of magnitude apart. §8.2 fired on 41 dossiers and 10 drew a complaint. §8.6, the wrong channel, fired on 21 and 1 did.

![Figure 3. The 16 complaints, one square each. Red: the play broke §8.2.](figures/complaints.png)

*Figure 3. The 16 complaints, one square each. Red squares broke §8.2.*

Wasted escalations can only occur among the 294 routed signals. Owners marked 39% of those wasted.

- No rule separated them. Off-hours paging: 44% against 38%. Routing below the floor: 41% against 38%.
- A first draft found these rules doubling waste. That was a denominator error, routed against unrouted. The same selection effect sits under every outcome field.
- The deserved verdict did separate them. Owners marked 25% of routed deserved signals wasted (23 of 91), against 45% of routed signals that met no condition (92 of 203).

### The risk score ranks complaints and fails to rank waste

The area under the ROC curve (AUC) is the probability that a random harmful dossier scores above a random harmless one. 0.5 is chance.

- On all 629 dossiers: AUC 0.68 against wasted escalations, 0.80 against complaints. Four of the ten conditions fire only on routed dossiers, so these figures partly measure routing.
- On the populations where the harms occur: 0.52 against wasted escalations among the 294 routed, 0.68 against complaints among the 208 routed customer-visible. The complaint figure comes almost entirely from the §8.2 condition.
- Against churn: 0.52.
- The complaint figure is unstable. On the pre-registered split it is 0.89 on the development half and 0.61 on the held-out half, because only 16 events exist.

### Ungrounded numbers mark exaggerated real declines, and the most frequent rule is harmless

Among routed signals the wasted rate was 40% with an M6 finding and 39% without. The specification's warning that ungrounded numbers are "the most common way this system wastes attention" does not hold here. What M6 marks depends on what the corrected data shows (Figure 4):

- Real decline, overstated by the agent (121 dossiers): the routed accounts churned or downgraded at 53% (n = 47).
- Decline manufactured by the pipeline (58 dossiers): 12% (n = 17).

![Figure 4. M6 findings split by what corrected telemetry shows, with the share of routed dossiers that later churned or downgraded.](figures/m6_split.png)

*Figure 4. M6 findings split by what the corrected telemetry shows.*

Q2, the hypothesis-fit rule, fires on 80% of dossiers and separates nothing:

- Within each detector, churn and wasted rates match with or without it.
- Among unrouted signals, churn is 26% either way.
- Annotators flag a wrong hypothesis on 19% of Q2 dossiers against 22% of the rest.
- A hand check of 20 random Q2 findings found 17 correct, 2 false alarms and 1 unclear.

The rule is right about the dossiers, and the dossiers do not matter.

### A specification-anchored routing policy trades coverage for precision

I ranked every signal into four tiers: specification obligation, verified decline, other, pipeline artefact. I routed the first two under the 5-per-owner-week cap. Table 3 compares that policy with the agent on the populations where each measure exists.

| measure | agent | policy |
|---|---|---|
| signals routed | 294 | 189 |
| routed signals that later churned or downgraded | 30% | 39% |
| wasted, within the agent's routed set: the 186 the policy drops vs the 108 it keeps | 46% | 27% |
| complaints, same two halves | 6.5% | 3.7% |
| accounts with realised loss reached | 97% | 74% |
| realised loss unreached | | $3.02M of $11.66M |

*Table 3. Agent versus policy. Complaint and wasted fields are unobservable for the 81 signals the agent never routed. Those rows therefore compare the two halves of the agent's own routed set.*

- The policy routes 105 fewer signals. The ones it keeps churn more often and waste less.
- The cost is coverage. 16 accounts that churned or downgraded get no policy route: $3.02M of realised loss. The agent had reached 14 of those 16 and lost them anyway.
- Attribution is uncertain on 17 of the 26 signals involved and absent on the other 9. 17 had a concurrent owner intervention. The $3.02M is what the policy would not have seen, not what it would have lost.
- The agent's own severity ordering ranks as well as mine at equal volume (40% against 39% churn). It drops 45% of the specification's obligations.

## Limitations

1. **The least validated component supports the most frequent finding.** Q2's text test uses the default 0.35 threshold on topic labels with 1 to 5 fixtures each. 10 of the 12 label thresholds never moved off the prior, because the fixtures were too few. The 20-sample hand check gives a precision near 0.9, with a 95% interval of about 0.7 to 0.97. Both misses were near-verbatim statements of the hypothesis that the scorer put below threshold.
2. **Two scores depend on what they judge.** The quality score derives from the violations and can never serve as evidence about one. The deserved verdict reads the agent's own severity for 57 of its 163 positives. The quality slope comes from the annotators later used to report alpha, so that alpha is in-sample.
3. **The risk score is uncalibrated and partly selected on held-out data.** Its complaint AUC rests on 16 events and swings from 0.89 to 0.61 across the split. Its noisy-OR assumes independent conditions, and 111 dossiers fire two or more. Its condition set was chosen partly by held-out performance. That is model selection, even though the magnitudes were never fitted.
4. **The held-out split is spent.** It held 10 positives. I read it four times. Further tuning needs fresh labels.
5. **The text layer is a list of conventions.** The quote splitter and the signature tagger recognise the email conventions this corpus uses. Anything else reads as current text. Blocks over about 512 tokens are scored truncated. The model reads English hypotheses only, though it scored the four other languages in the fixtures correctly. New dossiers outside this corpus need the model, a 700 MB download.
6. **The outcome data limits every comparison.** Complaint and wasted fields exist only for routed signals. The outcomes file gives contradictory renewal outcomes to 92 of 176 accounts, so loss is counted once per account at its worst outcome. Attribution is uncertain wherever anything happened. No comparison here is causal.
7. **The evaluator sees only dossiers that exist, and some bad dossiers break no rule.** 230 real API-call drops, 352 real seat drops and 48 written triggers never became a dossier. sig_0385 was a wasted escalation with zero violations and a quality score of 1.00.
8. **One deployment, synthetic data.** No claim here extends beyond this corpus.

## If you had 3 months

1. **Instrument the detection stage.** Log every detector firing, not only the ones that became dossiers. First-stage recall becomes a measured number, and the 48 missed triggers become a dashboard series. Every downstream recall figure is bounded by this one.
2. **Make attribution a design, not a field.** Randomise routing on the margin: in the tier-3 band only, never on a specification obligation. Run any new policy in shadow beside the agent before a queue changes. Log owner actions at decision time. Without this, no comparison between policies is causal, and the $3.02M above stays a bound.
3. **Treat labels as a versioned instrument.** Adjudicated annotation of about 30 dossiers a week against a written rubric. Report kappa, PABAK and the intraclass correlation per batch. Handle rubric changes as migrations that re-grade what was graded. Retire a held-out set once it has been read.
4. **Run the rules as continuous integration.** Every specification rule is already a test. Run the 29 on each agent change and alert on drift in per-rule rates. The containment rules (§8.2, §8.3, §8.4, §8.7) gate deploys, because those have a measured cost.
5. **Calibrate the risk score and state the operating point.** Fit an isotonic calibration on rolling outcomes. Report Brier score beside AUC. Evaluate each harm only on the population where it can occur. Publish precision at each owner's capacity, not a corpus-wide number.
6. **Close the loop per violation class.** Each class gets an owner and a fix. The appended-email fabrication in 17 dossiers is one prompt change. Re-run the corpus after each fix. Ship the tiered ranking behind the per-owner budget, and measure wasted rate and complaint rate on the overlap set.
7. **Price the slot.** Until Cartogram states what a CSM slot is worth against a missed churn, precision and coverage can be measured but never traded. Everything above makes the measurement honest. Only that number makes the decision.

## Reproducing the numbers

1. `python analysis/run_all.py` (about two minutes, from the committed label cache).
2. `python analysis/facts.py`.
3. `python analysis/annotator_agreement.py`, `python analysis/violations_stats.py`, `python analysis/attention_budget.py`, `python analysis/detector_coverage.py`.
4. `python -m pytest -q` runs the 751 tests.

## References

1. Signal Labs. Agent specification (`spec.pdf`) and domain guide (`docs/domain.md`), 2026.
2. Cohen, J. A coefficient of agreement for nominal scales. *Educational and Psychological Measurement*, 1960. Krippendorff, K. *Content Analysis*, 2004. Byrt, T., Bishop, J., Carlin, J. Bias, prevalence and kappa. *J Clin Epidemiol*, 1993.
3. Terwee, C. et al. Quality criteria for measurement properties of health status questionnaires. *J Clin Epidemiol*, 2007.
4. FIRST. Common Vulnerability Scoring System v3.1 specification, §7.4, 2019. Guyatt, G. et al. GRADE guidelines: rating the quality of evidence, indirectness. *J Clin Epidemiol*, 2011.
5. Laurer, M. et al. DeBERTa-v3-base-zeroshot-v2.0, Hugging Face model card, 2024. Shankar, S. et al. Who validates the validators? Aligning LLM-assisted evaluation of LLM outputs with human preferences (EvalGen), 2024.
6. Manning, C., Raghavan, P., Schütze, H. *Introduction to Information Retrieval*, ch. 8, 2008. Elkan, C. The foundations of cost-sensitive learning. *IJCAI*, 2001.
