"""
ABOUTME: The truth layer. Indexes accounts/owners/artefacts, cleans telemetry (dedup, documented
ABOUTME: event corrections, bad-row flags), builds the cohort baseline, and serves semantic labels
ABOUTME: for any text (artefact or bare quote) through a text-hash cache.
"""

import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from . import spec
from .classifier import HYPOTHESES_HASH, NLI_MODEL, NLI_THRESHOLD, TextClassifier, is_stale, text_key
from .util import day, median, ts


class Context:
    """Everything a check may consult beyond the dossier itself. Works empty (cold path) or loaded."""

    def __init__(self, *, use_classifier=True, label_scope="evidence", label_cache_path=None,
                 legacy_double_count_end=spec.LEGACY_DOUBLE_COUNT_END, p95_step_date=spec.P95_STEP_DATE,
                 p95_factor=spec.P95_FACTOR, ingest_gap=spec.INGEST_GAP, ingest_gap_regions=spec.INGEST_GAP_REGIONS):
        self.loaded = False
        self.accounts = {}
        self.owners = {}
        self.artifacts = {}
        self.tel = {}                        # account_id -> {date -> corrected row}
        self.cohort = {}                     # region -> {date -> {metric: median, _n: accounts}}
        self.by_account = defaultdict(list)  # account_id -> [(opened_at, detector, signal_id)]
        self.account_triggers = defaultdict(list)  # account_id -> [(timestamp, artifact_id, {trigger,...})]
        # semantic labels: text_key -> label dict. Filled lazily; persisted to label_cache_path if given.
        self.use_classifier = use_classifier
        self.label_scope = label_scope       # "evidence" (artefacts cited by dossiers) or "all"
        self.label_cache_path = Path(label_cache_path) if label_cache_path else None
        self._labels = {}
        self._indexed = set()
        self._cache_dirty = False
        self._classifier = None
        self.labels_cover_corpus = False
        self.classifier_active = None        # None = not yet tried
        # deployment-specific telemetry events (docs/domain.md); overridable for other deployments
        self.legacy_end = legacy_double_count_end
        self.p95_step = p95_step_date
        self.p95_factor = p95_factor
        self.ingest_gap = ingest_gap
        self.ingest_gap_regions = set(ingest_gap_regions)
        self._load_cache()

    # ── loading ──────────────────────────────────────────────────────────────
    def load(self, accounts, owners, telemetry, artifacts, dossiers):
        self.accounts = {a["account_id"]: a for a in (accounts or [])}
        self.owners = {o["owner_id"]: o for o in (owners or [])}
        self.artifacts = {a["artifact_id"]: a for a in (artifacts or [])}
        for d in dossiers or []:
            if d.get("opened_at"):
                self.by_account[d["account_id"]].append((ts(d["opened_at"]), d.get("detector"), d.get("signal_id")))
        self.tel = self._clean_telemetry(telemetry or [])
        self.cohort = self._cohort_baseline(self.tel)
        if self.use_classifier:
            self._label_artifacts(dossiers or [])
        else:
            self._update_coverage()
        self.loaded = True
        return self

    def _clean_telemetry(self, telemetry):
        # 1. dedup (account, date) keeping the latest ingested_at — backfill rows supersede
        latest = {}
        for r in telemetry:
            k = (r.get("account_id"), r.get("date"))
            if None in k:
                continue
            if k not in latest or (r.get("ingested_at") or "") > (latest[k].get("ingested_at") or ""):
                latest[k] = r
        tel = defaultdict(dict)
        for (acct, ds), r in latest.items():
            row = dict(r)
            d = day(ds)
            acc = self.accounts.get(acct, {})
            api, dau = row.get("api_calls") or 0, row.get("dau_seats") or 0
            # 2. domain.md: legacy collector double-counted api_calls before the migration date
            if acc.get("collector") == "legacy" and self.legacy_end and d < self.legacy_end:
                row["api_calls"] = api / 2.0
            # 3. domain.md: p95 instrumentation stepped ×factor; rescale earlier values up
            if self.p95_step and d < self.p95_step and row.get("query_p95_ms") is not None:
                row["query_p95_ms"] = row["query_p95_ms"] * self.p95_factor
            # 4. bad rows: dead collector (all zero, status still ok), degraded, or dau above contracted seats
            dead = api == 0 and dau == 0
            degraded = row.get("ingest_status") == "degraded"
            seats = acc.get("seats_contracted")
            over = bool(seats) and dau > seats * spec.SEATS_OVERSHOOT_TOL
            row["usable"] = not (dead or degraded or over)
            row["dead"] = dead
            row["bad_reason"] = ("dead collector" if dead else "degraded" if degraded
                                 else "dau_seats above contracted seats" if over else None)
            tel[acct][d] = row
        # 5. missing dates stay missing — never zero-filled
        return tel

    def _cohort_baseline(self, tel):
        """Per-region daily median of each metric over usable rows. A move the whole region
        shares on the same days is a calendar or pipeline event, not one customer's behaviour."""
        buckets = defaultdict(lambda: defaultdict(list))
        for acct, days in tel.items():
            region = self.accounts.get(acct, {}).get("region", "ALL")
            for d, row in days.items():
                if not row["usable"]:
                    continue
                for metric in spec.TELEMETRY_METRICS:
                    if row.get(metric) is not None:
                        buckets[(region, d)][metric].append(row[metric])
                        buckets[("ALL", d)][metric].append(row[metric])
        cohort = defaultdict(dict)
        for (region, d), ms in buckets.items():
            entry = {m: median(v) for m, v in ms.items()}
            entry["_n"] = max((len(v) for v in ms.values()), default=0)
            cohort[region][d] = entry
        return cohort

    # ── labels ───────────────────────────────────────────────────────────────
    def _load_cache(self):
        if not (self.label_cache_path and self.label_cache_path.exists()):
            return
        try:
            data = json.loads(self.label_cache_path.read_text())
        except Exception:
            return
        meta = data.get("_meta", {})
        if (meta.get("model"), meta.get("threshold"), meta.get("hypotheses")) == (NLI_MODEL, NLI_THRESHOLD, HYPOTHESES_HASH):
            self._labels = {k: v for k, v in data.items() if k != "_meta"}

    def save_cache(self):
        if self.label_cache_path and self._cache_dirty:
            self.label_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(self._labels)
            payload["_meta"] = {"model": NLI_MODEL, "threshold": NLI_THRESHOLD, "hypotheses": HYPOTHESES_HASH}
            self.label_cache_path.write_text(json.dumps(payload))
            self._cache_dirty = False

    def _classifier_ready(self):
        if not self.use_classifier:
            return False
        if self._classifier is None:
            self._classifier = TextClassifier()
            self.classifier_active = self._classifier.available
        return self._classifier.available

    def label_text(self, text, account=None, mentions_other_account=False):
        """Label any text. Cache hit → no model call. Model unavailable → unverifiable."""
        if not text or not str(text).strip():
            return {"unverifiable": True, "reason": "empty text"}
        key = text_key(text, account)
        if key in self._labels:
            return self._labels[key]
        if not self._classifier_ready():
            return {"unverifiable": True, "reason": "classifier unavailable"}
        lab = self._classifier.label(text, account, mentions_other_account)
        if not lab.get("unverifiable"):
            self._labels[key] = lab
            self._cache_dirty = True
        return lab

    def label_artifact(self, artifact):
        """Label an artefact (lazily, cache-first); `stale` is decided here because it depends on who
        wrote it. Every labelled artefact is also indexed by account for the unattached-trigger scan,
        so artefacts first seen inside evaluate() are covered too."""
        text = ((artifact.get("subject") or "") + "\n" + (artifact.get("text") or "")).strip()
        lab = self.label_text(text, self.accounts.get(artifact.get("account_id")), bool(artifact.get("mentions_other_account")))
        if lab.get("unverifiable"):
            return lab
        lab = dict(lab, stale=is_stale(lab, artifact.get("author_type")))
        self._index_triggers(artifact, lab)
        return lab

    def _index_triggers(self, art, lab):
        aid = art.get("artifact_id")
        if not aid or aid in self._indexed or lab.get("stale") or lab.get("triggers_belong_elsewhere") or not art.get("timestamp"):
            return
        self._indexed.add(aid)
        trig = set()
        if lab.get("cancel_intent") and art.get("author_type") == "customer":
            trig.add("cancel_intent")
        if lab.get("legal_reference"):
            trig.add("legal_reference")
        if lab.get("security_incident") and art.get("author_type") == "customer":
            trig.add("security_incident")
        if lab.get("departure"):
            trig.add("buyer_or_champion_departure")
        if trig:
            self.account_triggers[art.get("account_id")].append((ts(art["timestamp"]), aid, trig))

    def _label_artifacts(self, dossiers):
        """Pre-warm: label the non-bot artefacts in scope (cited as evidence, or the whole corpus).
        Optional — evaluate() labels anything it meets on demand. Cache is flushed every 200 labels."""
        if self.label_scope == "all":
            ids = list(self.artifacts)
        else:
            ids = {ev.get("artifact_id") for d in dossiers for ev in d.get("evidence", []) or []}
        for n, aid in enumerate(ids, 1):
            art = self.artifacts.get(aid)
            if art and art.get("author_type") != "bot":
                self.label_artifact(art)
            if n % 200 == 0:
                self.save_cache()
        self.save_cache()
        if self.classifier_active is None:
            self.classifier_active = bool(self._labels)
        self._update_coverage()

    def _update_coverage(self):
        """Does the label cache cover (nearly) every non-bot artefact in the loaded corpus?"""
        non_bot = [a for a in self.artifacts.values() if a.get("author_type") != "bot"]
        if not non_bot:
            self.labels_cover_corpus = False
            return
        covered = sum(1 for a in non_bot if text_key(((a.get("subject") or "") + "\n" + (a.get("text") or "")).strip(),
                                                    self.accounts.get(a.get("account_id"))) in self._labels)
        self.labels_cover_corpus = covered >= 0.9 * len(non_bot)

    # ── per-dossier lookups ──────────────────────────────────────────────────
    def account_facts(self, d):
        """Account record wins over the dossier's metadata snapshot; disagreements are recorded."""
        md = d.get("metadata") or {}
        acc = self.accounts.get(d.get("account_id")) if self.loaded else None
        src = acc or md
        facts = {
            "arr": src.get("arr_annual", md.get("arr_annual")),
            "floor": src.get("materiality_floor", md.get("materiality_floor")),
            "flags": set((acc or {}).get("flags") or md.get("account_flags") or []),
            "region": src.get("region"),
            "collector": src.get("collector"),
            "seats": src.get("seats_contracted", md.get("seats_contracted")),
            "renewal_date": src.get("renewal_date", md.get("renewal_date")),
            "owner_id": md.get("owner_id"),
            "name": (acc or {}).get("name"),
            "mismatch": [],
        }
        if acc:
            for k in ("arr_annual", "materiality_floor"):
                if acc.get(k) != md.get(k):
                    facts["mismatch"].append(f"{k}: metadata={md.get(k)} account={acc.get(k)}")
            if set(acc.get("flags") or []) != set(md.get("account_flags") or []):
                facts["mismatch"].append(f"flags: metadata={md.get('account_flags')} account={acc.get('flags')}")
        return facts

    def owner_facts(self, d):
        md = d.get("metadata") or {}
        o = self.owners.get(md.get("owner_id")) if self.loaded else None
        return {
            "tz": (o or {}).get("timezone", md.get("owner_timezone")),
            "locale": (o or {}).get("locale", md.get("owner_locale")),
            "channel": (o or {}).get("preferred_channel", md.get("owner_preferred_channel")),
        }

    def window(self, rows, end, w):
        """(usable_rows, n_missing, n_bad, bad_reasons) for the w days ending at `end`."""
        vals, missing, bad, reasons = [], 0, 0, Counter()
        for i in range(w):
            r = rows.get(end - timedelta(days=i))
            if r is None:
                missing += 1
            elif not r["usable"]:
                bad += 1
                reasons[r.get("bad_reason") or "non-ok"] += 1
            else:
                vals.append(r)
        return vals, missing, bad, reasons
