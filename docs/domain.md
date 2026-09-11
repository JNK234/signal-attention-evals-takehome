# Account-Risk Domain Brief

Cartogram Inc. is a B2B analytics company. It sells an embedded analytics and semantic-modelling
platform to mid-market and enterprise customers, on annual contracts with per-seat pricing.
Signal Labs operates the attention layer over Cartogram's customer data: it decides which
accounts deserve a human's time this week, and why.

This brief covers all the jargon used in the corpus, the policy rules the agent operates under,
and — importantly — the **known data events** in Cartogram's telemetry pipeline. If you are new
to customer-success or revenue-retention operations, read this section first.

---

## Glossary

### Acronyms

| Term | Full Form | Meaning |
|------|-----------|---------|
| **ARR** | Annual Recurring Revenue | Contracted revenue per year for an account. The denominator for almost every risk judgement. |
| **AE** | Account Executive | Sales owner. Owns the commercial relationship and the renewal negotiation. |
| **CSM** | Customer Success Manager | Post-sale owner. Owns adoption, health and escalation. The primary consumer of routed signals. |
| **DAU** | Daily Active Users | Here measured as `dau_seats` — distinct seats that used the product on a given day. |
| **DPA** | Data Processing Addendum | Contractual annex governing how customer data is handled. A blocked DPA can suspend data loads. |
| **MRR** | Monthly Recurring Revenue | ARR / 12. A frequent source of twelve-fold arithmetic errors. |
| **NPS** | Net Promoter Score | Survey score 0–10. 0–6 is a detractor, 7–8 passive, 9–10 promoter. |
| **PO** | Purchase Order | The customer's internal authorisation to pay. A PO mismatch stalls an invoice without any dissatisfaction being involved. |
| **QBR** | Quarterly Business Review | Scheduled review meeting with the customer. Meeting notes from these are a rich, and heavily templated, evidence source. |
| **RCA** | Root Cause Analysis | Written post-incident explanation. Customers who start demanding RCAs in writing have usually stopped trusting the verbal version. |
| **SLA** | Service Level Agreement | Contractual performance commitment. Breach can trigger credits, and occasionally a claim of material breach. |
| **TTA** | Time To Attention | Elapsed time from a detector firing to a human being notified. The metric the attention layer is ultimately judged on. |

### Account and Retention Terms

| Term | Meaning |
|------|---------|
| **Champion** | The person inside the customer who advocates for the product internally. Usually a practitioner, not a budget holder. Losing the champion is the single strongest leading indicator of churn in this book. |
| **Economic buyer** | The person who controls the budget line. Often not a user. Their departure inside a renewal window is a mandatory-route trigger. |
| **Churn** | Non-renewal. The account leaves entirely. |
| **Downgrade** | Renewal at materially lower ARR — usually fewer seats, sometimes a lower tier. Counts as partial churn. |
| **Expansion** | Renewal at higher ARR. |
| **Materiality floor** | The minimum ARR at risk that justifies spending CSM time on an account. Set by tier: $60,000 enterprise, $18,000 mid-market, $6,000 growth. A small number of accounts carry a negotiated override — always read the account record, never the tier default. |
| **Play** | The recommended human action attached to a routed signal: `csm_checkin`, `exec_escalation`, `commercial_review`, `solutions_review`, `renewal_risk_review`, `reliability_rca_share`, or `watch_only`. All of these except `watch_only` and `renewal_risk_review` are customer-visible. |
| **Customer-visible** | Whether the recommended play involves contacting the customer. The distinction matters because some accounts are under contact restrictions. |
| **Legal hold** | Account flag. The account is in active dispute or litigation. No customer-visible play is permitted. |
| **M&A quiet period** | Account flag. The account is inside an acquisition process. No customer-visible play is permitted. Reaching out during a quiet period is the most reliable way to generate a complaint in this corpus. |
| **Reference customer** | Account flag. The customer has agreed to public advocacy. Higher relationship value than ARR alone suggests. |
| **Land-and-stall** | An account that bought seats and never adopted. Shows up in telemetry as a usage cliff, but the correct hypothesis is `onboarding_failure`, and the correct play is enablement rather than escalation. |
| **Alert storm** | Multiple signals opened on the same account for the same underlying cause. The fastest way to make an owner mute the queue. |

### Attention Economics

Cartogram has **14 CSMs**. Each absorbs roughly **5 investigated signals per week** — that is the
whole budget: about 70 signals a week across a 180-account book, and the limit binds
per-owner, not just in aggregate. The corpus covers 21 weeks.

The binding constraint in practice is not the average, it is the peak. Signal volume in this
corpus is heavily bursty, and a week that produces more candidate signals than the team can
absorb forces a triage decision whether or not anyone makes it deliberately.

Both error directions have a price, and they are not symmetric:

- A **false positive** costs a CSM slot, and if the play is customer-visible it can spook a
  healthy account. Complaints in this corpus concentrate on customer-visible plays against
  accounts that were never at risk.
- A **false negative** costs the ARR. A missed churn on an enterprise account is worth
  hundreds of CSM-hours.

An attention layer with 100% recall and 20% precision is not obviously better than one with
60% recall and 60% precision. Which one is better depends on the budget, and arguing that
trade-off with numbers is part of this assignment.

### Data Terms

| Term | Meaning |
|------|---------|
| **Detector** | A rule that opens a signal. Eight of them, defined in the specification. Deliberately high-recall. |
| **Dossier** | The complete record of one signal: its lifecycle, hypotheses, evidence, claims, actions and decision. The unit of evaluation. |
| **Hypothesis** | The agent's committed explanation for an anomaly. One of seven classes. |
| **Enrichment** | An asynchronous request for additional context (extended metric windows, account history). Can time out. |
| **Artefact** | Any single piece of text evidence: a ticket, an email, a chat message, a CRM note, meeting notes, a survey response, a security review, a billing event or a bot alert. |
| **Restricted artefact** | An artefact whose contents are not cleared for the account team — security reviews, vendor risk assessments, legal correspondence. Marked `restricted: true`. |
| **Collector** | Which telemetry agent version an account is on: `legacy` or `v2`. This matters. See below. |
| **Attribution** | Whether a specific signal can be credited for an outcome. Usually `uncertain`, because owners run their own plays in parallel. |
| **Trajectory** | (Not in the data.) The underlying truth about whether an account's usage really moved. You are being asked to infer this. |

---

## Policy Rules

1. **Mandatory route**: written cancellation intent, legal or regulatory references, customer-raised
   security incidents, economic-buyer departure within 90 days of renewal, and billing disputes
   above 5% of ARR must all reach a human. They must never be suppressed — **including when the
   telemetry looks completely healthy.**
2. **Contact restrictions**: accounts flagged `legal_hold` or `mna_quiet_period` must not receive a
   customer-visible play. Internal routing only.
3. **Restricted-source containment**: never quote a `restricted: true` artefact verbatim in a dossier
   routed to the account team. Noting that the review exists is fine.
4. **Cross-tenant isolation**: evidence must belong to the account the signal is about.
5. **Confidence honesty**: `high` confidence requires at least two distinct evidence sources.
6. **Notification fit**: notify on the owner's declared channel, in their declared locale, inside
   08:00–19:00 in **their** timezone. `P0` may page at any hour.
7. **Contact detail handling**: do not carry raw customer email addresses or phone numbers into a
   routed dossier.

---

## Known Platform Data Events

**Read this section before you trust a single number in `telemetry.jsonl`.**

Cartogram's telemetry pipeline has a history. Three events inside the corpus window are documented
and known to the engineering team. They are not bugs in the data you were given — they are the
data, and any correct analysis has to account for them.

| Date | Event | Effect |
|------|-------|--------|
| **2026-05-18** | Telemetry pipeline migration | Before this date, `api_calls` were **double-counted** for every account on the `legacy` collector. On 2026-05-18 the double-counting stopped. Uncorrected, these accounts show an apparent ~50% collapse in API traffic on a single day, with no change in customer behaviour whatsoever. Check the `collector` field. |
| **2026-04-27** | Latency instrumentation change | `query_p95_ms` changed from a mean-of-p95 approximation to a true p95, across all accounts. Values step up by roughly 1.8× on this date. Latency did not get worse. |
| **2026-06-11 → 2026-06-13** | Regional ingest incident | Rows for `apac` and `emea` accounts were dropped entirely for three days. These dates are **absent** from the file rather than zeroed. Any window function that treats a missing row as zero will read this as a total outage. |

Three further properties of the data are **not** documented anywhere in Cartogram's runbooks, and
you will have to find them yourself:

- Some `(account_id, date)` pairs appear **more than once**. These are backfill corrections; the row
  with the latest `ingested_at` supersedes the earlier one. The `ingest_status` field on a
  correction reads `backfill`. Summing without deduplicating inflates totals.
- The `ingest_status` field is **not reliable**. A small number of accounts have windows where every
  metric is genuinely zero while the status still reads `ok`. Product usage did not stop; the
  collector did.
- `dau_seats` for `apac` accounts is bucketed on the UTC day. Their weekend shape is therefore
  shifted relative to their actual local working week.

Separately, note that a usage decline is not automatically a risk. Several accounts in this book
declined for reasons that are entirely expected: contracted downsizes recorded in CRM notes,
industry seasonality (retail troughs after the holiday period, edtech troughs over summer,
public-sector spikes at fiscal year end), and scheduled customer holidays that CSMs flagged in
advance. The evidence for all of these is in `artifacts.jsonl`.

---

## Reading the Text Corpus

`artifacts.jsonl` is messy in specific, adversarial ways. Some of what you will find:

- **Quoted history.** Email threads where the new message is positive ("resolved, thanks") and the
  quoted text below it repeats an alarming complaint from months earlier. Keyword matching on the
  full body will fire on the quote.
- **Automated noise.** `bot_alert` artefacts are machine-generated monitoring and deploy posts. Many
  read like incidents and are explicitly self-resolving. They are the largest single source of text
  volume and carry almost no signal.
- **Templated severity.** Support ticket `priority` is customer-set and inflated by policy at several
  accounts. `P1` on a ticket does not mean what `P1` means on a signal.
- **Sarcasm.** A handful of chat messages from friendly champions are jokes, and say so.
- **Forwarded mentions of other accounts.** Some artefacts name a *different* customer inside
  forwarded text, sometimes with that customer's churn described. Attributing that to the account
  the artefact belongs to is a mistake.
- **Code-switching and locale.** Around 14% of customer-authored artefacts mix English with German,
  Spanish, Portuguese, Japanese or Indian-English register.
- **Typos and voice-to-text artefacts**, concentrated in CRM notes written on mobile after calls.
- **Near-duplicate tickets**, where two people at the customer filed the same issue hours apart.
- **Contact details in signature blocks.**
