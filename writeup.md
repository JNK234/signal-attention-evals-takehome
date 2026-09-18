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

**2. Zero-shot NLI model.** A natural-language-inference (NLI) model takes a passage and a written sentence. It returns how strongly the passage entails the sentence. Zero-shot means the model was never trained on this corpus. I chose DeBERTa-v3-base fine-tuned for zero-shot classification [5] (184 million parameters, runs offline) from a bake-off of three candidates on hand-written fixtures. It separated 11 of 12 labels with bimodal scores. It read German, Spanish, Portuguese and Japanese cancellation notices correctly. A multilingual NLI model scored almost everything near 1.0 and separated 7 of 12.

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

### How the three scores were built

All three scores came out of the same five-step loop, taken from the rubric-iteration procedure in my research notes [8] and run once per score:

1. **Source criteria from breakage.** Write the score from the specification alone, run it on all 629 dossiers, and list where it breaks: a floor, a zero-agreement statistic, a baseline that beats it. The checks come from observed failures, not from a guess about what could fail.
2. **Grade, and let the criteria move.** Score each candidate condition with an alignment measure against the annotators' majority vote, under a false-failure ceiling that is fixed before scoring. Alignment is the harmonic mean of coverage and one minus the false-failure rate [6].
3. **Version, do not freeze.** Split the labelled dossiers once, by hash, before any candidate exists. Tune only on the development half. Read the held-out half once. Treat each change as a new version, and re-grade the golden dossiers under it.
4. **Read the disagreement per slice.** Report agreement pairwise, never by pooling the evaluator into the human panel. Report PABAK beside kappa, and report each statistic per severity, per detector and per disposition. The diagnosis in every case came from a slice.
5. **Diagnose which failure it is.** A wrong verdict can come from the reader, when the text model scored a sentence below threshold. Or it can come from the rule, when the rule cannot express the case. The fix is different for each, so each recorded failure names which one it was.

In this section "rejected" means: I built or measured the candidate, it failed a stated test at one of these steps, and I recorded the failure. The subsections below tell that story for each score.

### The quality score

The formula is

    quality = Π over broken rules of (1 − 0.16 · w · c)

where w is the class weight (1.0, 0.6, 0.3 or 0.1). c is 1 for a confirmed finding and 0.5 for an uncertain one. There is one penalty per rule.

**The ranking came from the rules, before any annotator number entered.** The rule table and the severity classes were designed first, from the specification alone. With placeholder penalties the score already ranked dossiers the way the annotators did: Spearman 0.34, 0.54 and 0.40 against the three. Every step below changed the scale of the score, or its floor. None changed that ranking by more than 0.02. The annotators were used to check the scale at the end, not to build the score.

**Step 1: placeholder penalties, 0.45, 0.20, 0.10 and 0.05 per class.** These were chosen by feel to get a working evaluator. Result: the ranking above. Problem: the four numbers traced to nothing. A grader could ask why 0.45 and I had no answer.

**Step 2: a hard zero on any critical violation.** The specification calls its invariants hard rules, so I tested a gate: any critical finding sets quality to 0. Result: the annotators never do this. Their minimum quality scores are 0.30, 0.27 and 0.88, and none of them scored zero even on dossiers with fabricated evidence. A gate would put 0 where every human reader put 0.3. It also merges two axes the specification keeps apart: quality is how well the dossier was built, risk is how much harm it can cause. Rejected on both grounds. The vault notes that argue for a gate [7] were read and set aside for this reason.

**Step 3: a floor of 0.25.** A fixed floor keeps scores off zero. Result: it works, but the constant has no source, and every dossier below the floor gets the same score. Rejected as arbitrary.

**Step 4: a sum of penalties, clipped at zero.** The annotators' own scoring suggested this form. Regressing each annotator's quality score on the severities they recorded gives a straight line. The slopes are −0.161, −0.162 and −0.145. The R² values are 0.60, 0.82 and 0.70. So humans subtract a roughly fixed amount per finding. I implemented the sum. Result: 134 of 629 dossiers (21%) sat at exactly 0.0, two of them with no critical violation at all. sig_0313 broke six soft and medium rules and got the same score as a dossier with fabricated evidence. Terwee and colleagues [3] set the bar: when more than 15% of scores sit at the lowest value, the instrument has a floor effect and cannot distinguish those cases. Rejected on that bar.

**Step 5: exponential decay, exp(−penalty).** It removes the floor. Two searches, in the vault and outside it, found the form used only for search-relevance decay, with no measurement-theory basis. Withdrawn before implementation.

**Step 6: the product, Π(1 − penalty).** This is the form the Common Vulnerability Scoring System uses for its impact sub-score, 1 − (1−C)(1−I)(1−A) [4]. It can approach zero but never reach it by accumulation. Result: dossiers at the floor fell from 134 to 1. Distinct score values rose from 35 to 109. Spearman against each annotator moved by less than 0.015, so the ranking the rules produce was unchanged. Kept.

**Step 7: the magnitude.** The product still charged 0.50 per critical finding, three times the annotators' slope. The evaluator's median sat 0.29 below every annotator, with a mean absolute error of 0.32. I replaced the 0.50 with the one number the three annotators agree on: the slope, 0.16. The class ratios stayed the specification's 10:6:3:1. Result: median 0.54 to 0.80 against annotator medians 0.80, 0.83 and 0.96. Mean absolute error 0.32 to 0.12, inside the 0.11 to 0.17 the annotators show against each other. Spearman between the old and new scores is 0.96: this was a rescale, not a reordering. The three annotators agree on the cost of a finding to within 0.017 and disagree on their baseline by 0.15. That is why only the slope was borrowed and their intercepts were not.

**What is in-sample and what is not.** The slope was fitted to these annotators. Any statistic that depends on scale (bias, mean absolute error, Krippendorff's alpha) is therefore partly in-sample. The rank statistic, Spearman, is not, because the ranking came from the rules.

### The deserved-attention verdict

The specification never says positively what deserves attention. It states two obligations. §8.1 lists five triggers that must reach a human. §4.6 says a signal whose enrichment timed out must still reach a human. The shipped verdict is a three-row table: those two obligations, plus one inference from §6.3. It fires on 163 of 629 dossiers. Here is how it got there.

**Step 1: eight conditions, one of them from the specification.** The first version treated any grounded decline or any non-benign customer text as deserving. It fired on 215 dossiers. Result: it fired on a 25-day-old feature request marked "not urgent". Seven of the eight conditions were mine, not the specification's. Rejected: an evaluator that invents obligations cannot be defended against the document it audits.

**Step 2: the two obligations only.** This version fired on 106. Three variants were tested at this stage and rejected:

- A materiality gate, which drops a signal whose ARR at risk is below the floor. It raised agreement with the annotators from 72/68/65% to 77/70/70%. But the ARR-at-risk figure is the agent's own estimate, and 61 dossiers carry one that is provably wrong (M1 and M5). Letting the agent's number veto a route the specification requires would let the agent grade itself. Rejected. The five points of agreement were the price.
- A "severely below the floor" threshold instead of a hard gate. Agreement fell smoothly as the threshold moved from 1.0× to 0.1× of the floor, with no cliff anywhere. Rejected: there was no natural threshold to defend.
- A gate on high-quality dossiers only. It chains three outputs into one and scored between the other two. Rejected.

**Step 3: measure version 2 properly.** Against the three annotators its Cohen's kappa was 0.04, 0.10 and 0.02, all with 95% intervals crossing zero, which means no agreement. Its F1 against the annotators' majority vote was 0.19. Four trivial baselines beat it: every P0 or P1 signal (F1 0.57), every non-benign hypothesis (0.57), copy the agent's own routing (0.40), always yes (0.35). On the held-out half it caught 0 of 10 positives. A spec-only verdict was therefore measurably worse than reading the agent's own severity label. That result forced the search that follows.

**Step 4: pre-register a split, then generate candidates.** Before generating any candidate I split the 130 dossiers all three annotators labelled into 71 development and 59 held-out, by hashing the signal identifier. This follows the rubric-iteration procedure in the vault [8]: fix the held-out set before the first candidate, so no candidate can be tuned on it. I then scored 12 candidates with the alignment measure from EvalGen [6]. Alignment is the harmonic mean of two rates. Coverage is how many majority-yes dossiers the candidate catches. The false-failure rate is how often it says yes when the majority said no. The false-failure ceiling was fixed at 0.55 before scoring, because a false alarm burns one of a CSM's five weekly slots. Result: one candidate, "uncertain trigger or unattributed departure", passed on development at 0.39. On held-out it scored 0.09. The split caught an overfit. Rejected.

**Step 5: test vetoes and conjunctions.** The next round tested conditions that remove signals (renewal more than 90 days away, P2 or P3 severity, benign hypothesis) and conditions that combine. One conjunction, P0 or P1 and a non-benign hypothesis, aligned at 0.65 on development and 0.67 on held-out, with false-failure rates of 0.23 and 0.25. It was the only candidate to clear the ceiling on both halves. Then it was checked against the 18 golden verdicts derived by hand from the specification. It failed two: sig_0059 and sig_0441 claim a champion departure 197 and 109 days before renewal, and §8.1 bounds departure to 90 days. A global veto on renewals more than 90 days out fixed those two. It also broke sig_0202, a billing dispute 242 days out that §8.1 does require. At this point the decision was to keep the spec-only rule and record the near miss.

**Step 6: the full sweep with a significance test.** I then swept every one- and two-term combination of 14 atomic predicates, OR'd onto the spec rule: 106 policies. Each predicate was tagged as reading the agent (severity, confidence) or reading evidence (customer text, artefact claims, source count, fabricated evidence). Each policy was tested against the majority vote with a permutation test. A permutation test shuffles the labels 3,000 times to see how often chance alone reaches the same score. Each survivor had to clear the false-failure ceiling on both halves and keep 18 of 18 goldens. Result: every evidence-only predicate was noise, with p between 0.15 and 0.42. The only predicate with real signal was the agent's own severity. That is not circular, because §6.3 gives the severity meaning: P0 means act today, P1 means material risk with corroboration. The specification, not the agent, says a P1 signal is material.

**Step 7: the shipped row.** The third row is: the agent assigned P0 or P1 with a non-benign hypothesis, and a departure claim sits inside the 90-day window. The window clause is what makes the row consistent with the goldens. Result: alignment on development rose from 0.30 to 0.44 (permutation p = 0.026). F1 against the majority rose from 0.19 to 0.38. All 18 goldens held. Kappa against the three annotators rose to 0.12, 0.32 and 0.23. On the held-out half alignment was 0.29, which clears no ceiling, and is recorded as such. The code marks the row as an inference and a test asserts the marking.

**One predictor refused on principle.** The agent's own ARR-at-risk figure was tested as "at risk is at least 10% of annual". It was the strongest single signal on held-out data (p below 0.001). It is wrong on 59 dossiers. Used as an AND, it vetoes sig_0350, a golden true via §4.6 with ARR at risk at 7.6% of annual. Used as an OR, it re-breaks the departure window. The rule I kept: reading a self-report to add coverage is safe, reading one to withhold a route the specification requires is not [9]. A test asserts that the verdict never reads the field.

### The risk score

The README defines risk as the probability that the dossier causes harm. The domain guide names three harms: a customer complaint, a wasted CSM slot, and a missed churn. The shipped score is a noisy-OR over ten conditions, each with a probability tied to its severity tier. Here is how it got there.

**Step 1: seven additive terms.** The first version summed seven terms. Their conditions cited specification sentences. Their coefficients (0.5, 0.3, 0.4, 0.25, 0.2, 0.2, 0.15) cited nothing. It also had a 500,000-dollar ARR constant and a per-tier multiplier. Rejected: the same problem as the quality placeholders, no source for any number.

**Step 2: fit the coefficients to outcomes.** The obvious fix is to fit each coefficient to its measured lift, for example the 9.6× complaint lift on §8.2. Rejected before fitting: the corpus has 16 complaints and 20 candidate rules. Sixteen events cannot support a weight per rule, and an evaluator that will run on dossiers nobody has seen must not encode one sample's accidents.

**Step 3: drop the tier multiplier.** Account tier changes how expensive a missed churn is, not how likely one is. The README defines the field as a probability. The multiplier moved to the attention-budget ranking, where cost belongs. Rejected here, kept there.

**Step 4: replace the sum with a noisy-OR.** A sum has no upper bound and double-charges. 111 of 629 dossiers fire two or more conditions. 15 of the 16 complaints fire two at once. A sum therefore charges one underlying event repeatedly. A noisy-OR combines independent causes of one outcome. If condition i causes harm with probability p_i, the probability that at least one does is

    risk = 1 − Π over fired conditions of (1 − p_i · c_i)

with c_i the certainty factor (1 confirmed, 0.5 uncertain). It stays in [0, 1]. It is the same algebra as the quality score. It has a precedent in medical diagnosis, the QMR-DT network [10]. Signal Labs' own public material also names noisy-OR, for combining alerts from models trained on the same data. Kept.

**Step 5: choose which conditions can cause harm.** A condition passed two tests. The specification must forbid the thing it fires on. That thing must plausibly cause one of the three harms. That gave ten:

- Six containment and integrity rules. §8.2: customer-visible play on a legal-hold or quiet-period account. §8.3: restricted artefact quoted to a human. §8.4: cross-tenant evidence. §8.7: contact details in a routed quote. I6: fabricated evidence a human read. §8.1: mandatory trigger suppressed.
- Two materiality rules: M6 (a number that reproduces only on uncorrected data, read by a human), M4 (routed below the materiality floor).
- Two judgments from the domain guide: a customer-visible play on an undeserving signal, and a deserving signal that never reached a human.

Five of these fire only if a human read the dossier, because a fabricated quote nobody saw cannot cause a complaint: 0 of the 389 dossiers with no customer-visible play drew one.

**Step 6: set the magnitudes without fitting.** The specification's §11 table ranks the severity classes but gives no numbers. I needed three probabilities: one for the critical tier, one for the high tier, one for the two judgments. The critical tier got 0.45 and the high tier 0.20. The ratio between them, 2.25×, borrows the ratio between CVSS's high and low impact levels, 0.56 and 0.22, which is 2.55× [4]. CVSS is the one widely used standard that combines rated conditions with this exact algebra, so its step size is the closest precedent. The judgments started at 0.30, above the high tier. That was wrong: GRADE, the framework for grading evidence, treats indirect or inferred evidence only as a reason to rate confidence down, never up [11]. A condition the specification never rates cannot outweigh one it calls high. The judgments moved to 0.15. IEC 31010 says outright that the choice of an ordinal scale is to some extent arbitrary [12]. Its remedy is validation against known cases. That is what the next step does.

**Step 7: validate the unfitted score.** With no fitting, the score's quartiles ordered the outcomes: complaint rates of 0.0%, 0.0%, 3.2% and 7.0% from the lowest to the highest quartile, and all 16 complaints in the top half. The three dossiers at the highest value, 0.83, drew complaints at 67% against a 2.5% base. Nothing below 0.3 drew a complaint. The form and the magnitudes were kept.

**Step 8: diagnose the zeros.** 270 dossiers (43%) scored exactly 0.0, with only 13 distinct values across the corpus. 192 of the zeros were unrouted dossiers that caused no harm, which is correct. 78 were routed, and 24 of those caused harm. One, sig_0385, was a wasted escalation with zero violations and a quality score of 1.00: a ceiling on any rule-based approach. A weight-share audit showed why the routed zeros existed: the wasted-slot harm held 12% of the score's mass (only M6 could produce it) while the annotators raised wasted attention in 53% of their flags.

**Step 9: two sweeps for the missing condition.** The first sweep tried eight candidates in 93 combinations with the outcome AUC as the target. It failed. All eight together scored 0.738, below the best pair at 0.741. The best single candidate, "Q2 on a routed dossier", fired on 80% of the corpus with a lift of 0.99 against the annotators' wasted flag. It raised the AUC only by moving a large block of dossiers off zero at once. Lesson recorded: choosing conditions by AUC finds the biggest block, not the cause. The second sweep used the specification as the filter. Only the four materiality rules forbid something a routed dossier can do, so only those four were candidates. They ran on a second pre-registered split of all 629 dossiers: 326 development, 303 held-out. M4 was the only one with an effect. On the held-out half, zeros fell from 43% to 41% and the wasted-escalation AUC rose from 0.62 to 0.64. M4 was added. Three candidates were rejected by name. "Q2 routed" and "M5 routed": their only argument was the AUC gain. M1 and M3: they fire on 8 and 7 dossiers and change nothing.

**Step 10: fix double-charging.** Two conditions describe one event: a suppressed mandatory trigger (§8.1) and a deserving signal that reached no human. They co-occur 5.0× more often than independence predicts. They are grouped and charged once, at 0.45 instead of 0.62. A second pair, fabricated evidence (I6) and leaked contact details (§8.7), co-occurs 10.2× chance and was deliberately not grouped: one destroys trust, the other breaches containment, and they are different harms. During this step a bug surfaced: 35 of the 62 §8.1 findings were uncertain but had been charged in full. The certainty factor now flows into risk as it does into quality.

**Result.** Zeros fell from 270 to 255. Distinct values rose from 13 to 25. The AUC against wasted escalations rose from 0.65 to 0.68. The complaint AUC stayed at 0.80. The noisy-OR still assumes independence, and the residual correlation between ungrouped conditions is a known overstatement, not a solved problem. Which conditions exist was chosen partly by held-out performance. That is model selection, even though no magnitude was ever fitted, and Limitations records it.

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

### The four statistics, defined

This subsection defines the statistics. The results follow in the next subsections and in Findings.

- **Cohen's kappa** is agreement between two raters after subtracting the agreement chance alone would produce from their yes-rates. 0 is chance, 1 is perfect.
- **PABAK** (prevalence-adjusted bias-adjusted kappa) is two times the raw agreement minus one [2]. When both raters say "no" about 80% of the time, as here, kappa drops even when they agree often. PABAK ignores the base rate, so I report it beside kappa.
- **Spearman's rho** is the correlation between two raters' rankings of the dossiers. It measures order and ignores level.
- **Krippendorff's alpha** is chance-corrected agreement for any number of raters [2]. On a 0-to-1 score it can treat the score two ways. Interval: 0.62 versus 0.63 is a small disagreement. Nominal: any difference is total disagreement.

`analysis/annotator_agreement.py` prints every number in this section.

### How much the annotators agree

On the 130 dossiers all three labelled:

- Deserved attention: pairwise kappa 0.44, 0.43 and 0.47, with bootstrap 95% intervals from about 0.23 to 0.63. PABAK 0.59, 0.59 and 0.57. Raw agreement 79%.
- Quality score: pairwise Spearman 0.43, 0.31 and 0.45. Krippendorff's alpha 0.186 as interval and −0.04 as nominal.

The nominal alpha means no agreement at all for anybody who reads the score as a grade. The interval alpha means the annotators disagree by small distances. Zapf and colleagues show the same swing on ordinal data scored as nominal [2].

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
- Every agreement number is reported per annotator and per slice. Plank's position paper on human label variation is the reason: disagreement concentrates at the decision boundary, and pooling hides where [13].

The evaluator lands below the human band: kappa 0.12, 0.32 and 0.23 against the three annotators, with the first interval crossing zero. Human agreement is a reference, not a ceiling [14], so the gap is reported and explained rather than clamped. PABAK is 0.40, 0.47 and 0.40. Severity explains the gap. The annotators say yes to 43% of P1 signals and 6% of P2, so they track the agent's severity label. The specification ignores that label. Adding the evaluator to the panel raises quality alpha from 0.186 to 0.295. That figure is in-sample, because the slope comes from these annotators. It shows consistency with them and nothing more.

## Findings

### The evaluator against the annotators

Table 1 puts the evaluator beside the human-versus-human numbers. F1 is the harmonic mean of precision and recall against the majority vote. MAE is the mean absolute difference between two raters' quality scores.

| statistic | human vs human | evaluator vs annotators | what it means |
|---|---|---|---|
| deserved: kappa | 0.44 / 0.43 / 0.47 | 0.12 / 0.32 / 0.23 | Humans agree with each other at a moderate level. The evaluator agrees with annotators 2 and 3 at a weak level and with annotator 1 no better than chance. |
| deserved: PABAK | 0.59 / 0.59 / 0.57 | 0.40 / 0.47 / 0.40 | Once the 80% "no" base rate is removed, the evaluator sits about 0.15 below the human band, a smaller gap than kappa suggests. |
| deserved: F1 vs majority | | 0.38 | Of the signals the majority called deserving, the evaluator catches 36%, and 40% of the signals it calls deserving the majority agrees with. |
| quality: Spearman rho | 0.43 / 0.31 / 0.45 | 0.36 / 0.55 / 0.44 | The evaluator ranks dossiers by quality as consistently with each annotator as the annotators do with each other. |
| quality: MAE | 0.11 / 0.17 / 0.12 | 0.12 | The evaluator's quality score is on average 0.12 away from an annotator's, the same distance the annotators are from each other. |
| quality: median | 0.80 / 0.83 / 0.96 | 0.80 | The evaluator's typical score matches annotators 1 and 2. Annotator 3 grades everyone higher. |
| quality: Krippendorff alpha (interval) | 0.186 | 0.295 with the evaluator added | Adding the evaluator as a fourth rater raises the panel's agreement, so it disagrees with the humans less than they disagree with each other. In-sample, see Methodology. |

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
- No other rule made a difference among those 208 dossiers. For each rule I compared two complaint rates: the dossiers that broke it, and the dossiers that did not. The two rates were within a few points of each other every time. Examples: §6.3 (late notification) 8% against 8%. M6 (ungrounded number) 10% against 6%. Q2 (weak hypothesis) 7% against 9%.
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

I ranked every signal into four tiers: specification obligation, verified decline, other, pipeline artefact. I routed the first two under the 5-per-owner-week cap, which is precision at a fixed capacity rather than a threshold [15]. Table 3 compares that policy with the agent on the populations where each measure exists.

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

**Limits of the system as designed.**

1. **The rule table is hand-written.** The 29 rules were transcribed from this version of the specification. If the specification changes, a person must edit the table. The tests catch a rule that cites a section that no longer exists. They cannot catch a rule the new specification adds and the table omits.
2. **The text model is small and thinly calibrated.** DeBERTa-v3-base reads at most about 512 tokens per block, so longer blocks are scored truncated. Its label sentences are English. 10 of the 12 label thresholds never moved off the default, because there were too few fixtures to calibrate them. The hypothesis-fit rule Q2, which fires most often, rests on the least calibrated labels: a 20-sample hand check gave a precision near 0.9 with a 95% interval of 0.7 to 0.97. Both misses were near-verbatim statements of the hypothesis that the model scored below threshold. A larger model, or fixtures per topic, would raise that floor.
3. **The text layer is a list of conventions.** The quote splitter and the signature tagger recognise the email conventions this corpus uses. A convention they do not list reads as current text, so an old complaint quoted under a new message could fire a trigger.
4. **New dossiers need the model.** The committed cache covers this corpus. Any dossier outside it needs the 700 MB model on the grader's machine. Without it, four rules go unevaluated.
5. **The scores are coupled.** The deserved verdict reads the agent's own severity for 57 of its 163 positives. Two risk conditions read the deserved verdict, so a change to one table moves the other score: adding the §6.3 row moved 46 risk scores. The quality score derives from the violations and can never serve as evidence about one.
6. **The risk score is not a calibrated probability.** Its magnitudes come from a standard, not from this data. Its noisy-OR assumes independent conditions, and 111 dossiers fire two or more. Its complaint AUC rests on 16 events and swings from 0.89 to 0.61 across the split. Its condition set was chosen partly by held-out performance, which is model selection.

**Limits of the data.**

7. **Outcome fields exist only for routed signals.** Complaint and wasted-escalation fields are unobservable for the 335 signals no human saw. Every comparison of a policy against the agent is therefore made on the agent's own routed set, and no comparison here is causal.
8. **The outcomes file contradicts itself.** 92 of 176 accounts carry more than one renewal outcome. Loss is counted once per account at its worst outcome. Attribution is uncertain wherever anything happened.
9. **The evaluator sees only dossiers that exist.** 230 real API-call drops, 352 real seat drops and 48 written triggers never became a dossier. And some bad dossiers break no rule: sig_0385 was a wasted escalation with zero violations and a quality score of 1.00.
10. **One deployment, synthetic data.** No claim here extends beyond this corpus.

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

Sources marked (vault) have a full reading note in my research vault. The rest are cited from the original.

1. Signal Labs. Agent specification (`spec.pdf`) and domain guide (`docs/domain.md`), 2026. Signal Labs public material on noisy-OR: https://www.signallabs.ai/resources/blog/signal-labs-kickoff.
2. Cohen, J. A coefficient of agreement for nominal scales. *Educational and Psychological Measurement* 20(1), 1960. doi:10.1177/001316446002000104. Byrt, T., Bishop, J., Carlin, J. Bias, prevalence and kappa. *J Clin Epidemiol* 46(5), 1993. doi:10.1016/0895-4356(93)90018-V. Krippendorff, K. *Content Analysis*, 4th ed., Sage, 2018. Zapf, A. et al. Measuring inter-rater reliability for nominal data. *BMC Med Res Methodol* 16, 2016. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4974794/ (vault).
3. Terwee, C. et al. Quality criteria were proposed for measurement properties of health status questionnaires. *J Clin Epidemiol* 60(1):34-42, 2007. doi:10.1016/j.jclinepi.2006.03.012.
4. FIRST. Common Vulnerability Scoring System v3.1 specification, §7.4, 2019. https://www.first.org/cvss/v3.1/specification-document.
5. Laurer, M. et al. DeBERTa-v3-base-zeroshot-v2.0, model card, 2024. https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0.
6. Shankar, S., Zamfirescu-Pereira, J. D., Hartmann, B., Parameswaran, A., Arawjo, I. Who validates the validators? Aligning LLM-assisted evaluation of LLM outputs with human preferences. *UIST*, 2024. https://arxiv.org/abs/2404.12272 (vault).
7. HUD. Verifier and reward design for RL environments, 2025. https://www.hud.ai/resources/verifier-reward-design-rl-environments (vault). Argues for a hard gate on critical failures. Read and set aside, see the quality score.
8. Lambert, N. et al. Tülu 3: pushing frontiers in open language model post-training, 2024, §on dev and unseen evaluation splits. https://arxiv.org/abs/2411.15124 (vault). Phipson, B., Smyth, G. Permutation p-values should never be zero. *Stat Appl Genet Mol Biol* 9(1), 2010. doi:10.2202/1544-6115.1585.
9. Everitt, T., Hutter, M., Kumar, R., Krakovna, V. Reward tampering problems and solutions. *Synthese*, 2021. https://arxiv.org/abs/1908.04734 (vault). Manheim, D., Garrabrant, S. Categorizing variants of Goodhart's law, 2018. https://arxiv.org/abs/1803.04585 (vault).
10. Shwe, M. et al. Probabilistic diagnosis using a reformulation of the INTERNIST-1/QMR knowledge base. *Methods Inf Med* 30(4), 1991. doi:10.1055/s-0038-1634846.
11. Guyatt, G. et al. GRADE guidelines: 8. Rating the quality of evidence, indirectness. *J Clin Epidemiol* 64(12), 2011. doi:10.1016/j.jclinepi.2011.04.014.
12. IEC 31010:2019. Risk management, risk assessment techniques, Annex B.8.6. https://www.iso.org/standard/72140.html.
13. Plank, B. The "problem" of human label variation. *EMNLP*, 2022. https://arxiv.org/abs/2211.02570 (vault).
14. Richie, R., Grover, S., Tsui, F. Inter-annotator agreement is not the ceiling of machine learning performance. *BioNLP*, 2022. https://aclanthology.org/2022.bionlp-1.26.pdf (vault). Boguslav, M., Cohen, K. B. Inter-annotator agreement and the upper limit on machine performance. *Stud Health Technol Inform* 245, 2017. doi:10.3233/978-1-61499-830-3-298.
15. Elkan, C. The foundations of cost-sensitive learning. *IJCAI*, 2001. https://cseweb.ucsd.edu/~elkan/rescale.pdf (vault). Manning, C., Raghavan, P., Schütze, H. *Introduction to Information Retrieval*, §8.4, 2008. https://nlp.stanford.edu/IR-book/ (vault). Used in `attention_budget.md` for the ranking under a per-owner budget.
