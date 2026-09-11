# Data Dictionary

Every file is newline-delimited JSON (`.jsonl`), one record per line, UTF-8.
All timestamps are UTC and ISO-8601 with a trailing `Z`. All dates are `YYYY-MM-DD`.
All money is USD.

Join keys: `account_id` links everything; `signal_id` links dossiers to outcomes and
annotations; `artifact_id` links dossier evidence to the text corpus; `owner_id` links
accounts to owners.

---

## `data/accounts.jsonl` — 180 records

| Field | Type | Notes |
|---|---|---|
| `account_id` | str | `acct_0001` … `acct_0180` |
| `name` | str | Fictional customer name |
| `tier` | str | `enterprise` \| `mid_market` \| `growth` |
| `region` | str | `namer` \| `emea` \| `apac` \| `latam` |
| `industry` | str | 12 values. Four of them have strong in-window seasonality — see `domain.md` |
| `arr_annual` | int | Contracted annual revenue |
| `seats_contracted` | int | Licensed seats. `dau_seats` should not exceed this |
| `contract_start` | date | |
| `renewal_date` | date | Clusters on quarter boundaries |
| `owner_id` | str | Joins `owners.jsonl`. The person a signal on this account is routed to |
| `owner_role` | str | `csm` \| `ae` |
| `owner_timezone` | str | IANA name. The notification-window rule is relative to this |
| `owner_locale` | str | e.g. `en-US`, `de-DE`, `ja-JP` |
| `owner_preferred_channel` | str | `slack` \| `email` |
| `ae_id` | str | Sales owner, distinct from `owner_id` |
| `materiality_floor` | int | Minimum ARR at risk that justifies human time. **Read this field, do not infer it from `tier`** — some accounts carry a negotiated override |
| `flags` | list[str] | `legal_hold`, `mna_quiet_period`, `reference_customer`, `strategic`, `pilot`, `named_exec_sponsor`. May be empty |
| `collector` | str | `legacy` \| `v2`. Which telemetry agent the account is on. **This field is load-bearing** — see the 2026-05-18 event in `domain.md` |
| `economic_buyer` / `economic_buyer_title` | str | Budget holder |
| `champion` / `champion_title` | str | Internal advocate |

## `data/owners.jsonl` — 23 records

| Field | Type | Notes |
|---|---|---|
| `owner_id` | str | `u_001`–`u_014` are CSMs, `u_100`–`u_108` are AEs |
| `name` | str | |
| `role` | str | `csm` \| `ae` |
| `timezone` | str | IANA name |
| `locale` | str | |
| `preferred_channel` | str | `slack` \| `email` |

## `data/telemetry.jsonl` — 32,032 records

One record per account-day, except where noted. **Read the "Known Platform Data Events"
section of `domain.md` before using this file.**

| Field | Type | Notes |
|---|---|---|
| `account_id` | str | |
| `date` | date | 2026-02-01 … 2026-07-31 |
| `dau_seats` | float | Distinct seats active that day |
| `api_calls` | int | |
| `query_p95_ms` | int | Instrumentation changed on 2026-04-27 |
| `error_rate_pct` | float | Percentage, 0–100 |
| `dashboards_created` | int | |
| `data_volume_gb` | float | |
| `ingest_status` | str | `ok` \| `degraded` \| `backfill`. **Not fully reliable** |
| `ingested_at` | timestamp | When the row landed. Use this to resolve duplicates |

Notes on structure:
- **`(account_id, date)` is not unique.** Backfill corrections appear as a second row with a
  later `ingested_at` and `ingest_status: "backfill"`. The latest row supersedes earlier ones.
- **Rows are missing, not zeroed.** Scattered days are absent per account, and all `apac` and
  `emea` rows are absent for 2026-06-11 → 2026-06-13.
- **`collector` is not in this file.** It is on `accounts.jsonl` and you need it.

## `data/artifacts.jsonl` — 3,810 records

| Field | Type | Notes |
|---|---|---|
| `artifact_id` | str | `art_00001` … |
| `account_id` | str | The account this artefact belongs to |
| `type` | str | `support_ticket`, `email_thread`, `chat_message`, `crm_note`, `meeting_note`, `nps_response`, `security_review`, `billing_event`, `bot_alert` |
| `source` | str | Same as `type`. Distinct-source counts for the confidence rule use this |
| `restricted` | bool | `true` for security reviews and vendor risk assessments. Subject to a containment rule |
| `timestamp` | timestamp | |
| `author` | str | |
| `author_type` | str | `customer` \| `internal` \| `bot` |
| `lang` | str | `en` or a locale tag where the text code-switches |
| `subject` | str \| null | Present on tickets, emails, meeting notes, security reviews. **Often carries the operative sentence** — a cancellation notice may live entirely in the subject |
| `text` | str | The body |

Type-specific fields, present only where applicable:

| Field | On | Notes |
|---|---|---|
| `priority` | `support_ticket` | `P1` \| `P2` \| `P3`. **Customer-set and inflated.** Unrelated to signal severity |
| `status` | `support_ticket` | `open` \| `pending` \| `resolved` \| `closed` |
| `nps_score` | `nps_response` | 0–10 |
| `participants` | `email_thread` | |
| `channel` | `chat_message` | `#acct-*` is internal; `#cartogram-*-shared` is a shared channel with the customer |
| `disposition` | `crm_note` | |
| `mentions_other_account` | some | Set when forwarded text names a *different* customer. Present so you can tell the difference between an artefact that mentions another account and evidence that belongs to one |

## `data/signal_dossiers.jsonl` — 629 records

The unit of evaluation. One record per signal.

| Field | Type | Notes |
|---|---|---|
| `signal_id` | str | `sig_0001` … |
| `account_id` | str | |
| `detector` | str | Which of the eight detectors opened this signal |
| `detector_version` | str | `v2.1` \| `v2.3` \| `v3.0` |
| `opened_at` | timestamp | Time-to-attention is measured from here |
| `closed_at` | timestamp \| null | Null while the signal is still open |
| `lifecycle` | list | See below |
| `hypotheses` | list | See below |
| `evidence` | list | See below |
| `metrics_claimed` | list | See below |
| `actions` | list | See below |
| `notifications` | list | See below |
| `scoring` | object | `severity` (`P0`–`P3`), `confidence` (`low`/`medium`/`high`), `arr_at_risk` (int), `scored_at` |
| `decision` | object | `disposition`, `recommended_play`, `customer_visible` (bool), `reason` |
| `metadata` | object | Denormalised account and owner context, as the agent had it at decision time |

`lifecycle[]` — transition timestamps are strictly increasing, so the state at any instant is
unambiguous:

| Field | Notes |
|---|---|
| `step` | 0-based index. Violations you report should reference this |
| `from_state` / `to_state` | See the specification for the 11 states |
| `at` | Timestamp |
| `trigger` | `detector:<name>`, `agent_action`, or a system event: `enrichment_returned`, `enrichment_timeout`, `owner_acknowledged`, `human_preempt`, `staleness_timeout` |
| `reason` | Free text from the agent |

`hypotheses[]`: `step`, `hypothesis` (one of seven classes), `confidence`, `evidence_refs`
(list of `artifact_id`), `rationale`.

`evidence[]`: `step`, `artifact_id`, `source`, `restricted`, `attached_at`, `quote`. The
`quote` is the agent's excerpt. Whether it appears in the referenced artefact is a spec
question, not a given.

`metrics_claimed[]`: `step`, `metric`, `window_days`, `as_of` (date), `value_before`,
`value_after`, `claim` (human-readable string containing a signed percentage). Entries with
`metric: "arr_at_risk"` are restatements of the impact figure rather than telemetry claims.

`actions[]`: `step`, `action` (one of six), `at`, `params`.

`notifications[]`: `step`, `at` (UTC), `channel`, `locale`, `owner_id`, `attempt`. There is
deliberately no precomputed owner-local time.

`decision.recommended_play` is one of `watch_only`, `csm_checkin`, `exec_escalation`,
`solutions_review`, `commercial_review`, `renewal_risk_review`, `reliability_rca_share`.
`decision.disposition` is one of `routed`, `acknowledged`, `suppressed`, `expired`.

## `data/outcomes.jsonl` — 629 records

| Field | Type | Notes |
|---|---|---|
| `signal_id` / `account_id` | str | |
| `observed_at` | date | `2026-09-01` for all records |
| `renewal_outcome` | str | `renewed_flat`, `renewed_expansion`, `renewed_late`, `downgraded`, `churned`, `pending`. `pending` means the renewal date is after `observed_at` |
| `arr_delta` | int | Change in ARR at renewal. Negative for downgrade and churn |
| `attribution` | str \| null | `certain` \| `uncertain` \| `none`. Null when the outcome is `pending`. Mostly `uncertain` |
| `concurrent_interventions` | list[str] | Other things that happened to the account in the same window |
| `reached_human` | bool | Whether the signal was ever routed |
| `owner_acted` | bool | |
| `time_to_owner_action_hours` | float \| null | |
| `owner_marked_useful` | bool \| null | **Null whenever `reached_human` is false**, and null again where the owner never responded. This is a selected sample |
| `customer_complained_about_outreach` | bool | Rare. The clearest harm signal in the corpus |
| `escalation_was_wasted` | bool | An owner spent time on a signal that was not real |
| `post_hoc_root_cause` | str \| null | Retrospective cause, recorded for some bad outcomes only |

## `data/annotations/annotator_{1,2,3}.jsonl` — 240 records each

240 dossiers per annotator; 130 dossiers appear in all three files.

| Field | Type | Notes |
|---|---|---|
| `signal_id` | str | |
| `deserved_attention` | bool | The reviewer's judgment |
| `quality_score` | float | 0–1. Note the annotators use different decimal precision |
| `failure_points` | list | `step`, `category`, `severity` (0–1), `note` |
| `risk_flags` | list[str] | `policy_concern`, `missed_signal`, `wasted_attention`, `evidence_integrity`, `miscalibrated` |
| `overall_assessment` | str | One-line summary |
| `_annotator` | str | Which annotator |

`failure_points[].category` is drawn from a shared vocabulary: `fabricated_evidence`,
`missed_mandatory_route`, `policy_breach`, `state_machine_error`, `timing_violation`,
`overconfident_hypothesis`, `wrong_hypothesis`, `data_artifact_not_caught`,
`immaterial_signal`, `context_loss`, `duplicate_signal`, `other`. The three annotators use
this vocabulary with very different frequencies.
