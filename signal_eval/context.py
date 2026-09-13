"""
ABOUTME: The truth layer. Indexes accounts/owners/artefacts, cleans telemetry (dedup, documented
ABOUTME: event corrections, bad-row flags), computes paired-day changes and cohorts, and serves semantic labels
ABOUTME: for any text (artefact or bare quote) through a text-hash cache.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from . import spec
from .classifier import NLI_MODEL, NLI_THRESHOLD, TextClassifier, assemble, filled_hypotheses, is_stale, text_key
from .util import day, median, split_quoted, ts

CACHE_FLUSH_EVERY = 50      # new labels pending before save_cache() actually writes


def paired_change(rows, metric, end, w):
    """Week-over-week change from paired days: each day d of the w days ending at `end` is paired with d−w and
    the pair counts only when both rows exist and are usable. Missing days, weekend composition and the apac
    UTC-day shift then cancel out with no day-of-week model. pct = (Σafter − Σbefore) / Σbefore × 100 over the
    pairs; None when there are no pairs or Σbefore is 0. `excluded` counts the rows that cost a pair, by reason."""
    sum_after, sum_before, n, excluded = 0.0, 0.0, 0, Counter()
    for i in range(w):
        da = end - timedelta(days=i)
        pair = (rows.get(da), rows.get(da - timedelta(days=w)))
        ok = True
        for r in pair:
            if r is None or r.get(metric) is None:
                excluded["missing"] += 1
                ok = False
            elif not r.get("usable", True):
                excluded[r.get("bad_reason") or "non-ok"] += 1
                ok = False
        if ok:
            n += 1
            sum_after += pair[0][metric]
            sum_before += pair[1][metric]
    pct = (sum_after - sum_before) / sum_before * 100 if n and sum_before else None
    return {"pct": pct, "n_pairs": n, "sum_after": sum_after, "sum_before": sum_before, "excluded": dict(excluded)}


def _hub_cache_dir():
    """Where huggingface_hub keeps downloaded models, resolved the way the library does (env first)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def local_model_present(model_id=NLI_MODEL):
    """Is the model already on disk? A plain directory scan of the hub cache — imports nothing from
    transformers/huggingface_hub and never touches the network (README l.225-226: self-contained)."""
    snapshots = _hub_cache_dir() / f"models--{model_id.replace('/', '--')}" / "snapshots"
    try:
        return snapshots.is_dir() and any(p.is_dir() and any(p.iterdir()) for p in snapshots.iterdir())
    except OSError:
        return False


class Context:
    """Everything a check may consult beyond the dossier itself. Works empty (cold path) or loaded."""

    def __init__(self, *, use_classifier="auto", label_scope="evidence", label_cache_path=None,
                 legacy_double_count_end=spec.LEGACY_DOUBLE_COUNT_END, p95_step_date=spec.P95_STEP_DATE,
                 p95_factor=spec.P95_FACTOR, ingest_gap=spec.INGEST_GAP, ingest_gap_regions=spec.INGEST_GAP_REGIONS):
        self.loaded = False
        self.accounts = {}
        self.owners = {}
        self.artifacts = {}
        self.tel = {}                        # account_id -> {date -> corrected row}
        self.cohort_groups = {}              # (key, value) -> [account_id] for key in spec.COHORT_KEYS
        self.cohort_cache = {}               # (key, value, metric, end, w) -> {account_id: paired pct}
        self.by_account = defaultdict(list)  # account_id -> [(opened_at, detector, signal_id)]
        self.account_triggers = defaultdict(list)  # account_id -> [(timestamp, artifact_id, {trigger,...})]
        # semantic labels: text_key -> label dict. Filled lazily; persisted to label_cache_path if given.
        self.classifier_mode = use_classifier   # True | False | "auto" (as requested)
        self.label_scope = label_scope       # "evidence" (artefacts cited by dossiers) or "all"
        self.label_cache_path = Path(label_cache_path) if label_cache_path else None
        self._labels = {}
        self._indexed = set()
        self._cache_dirty = False
        self._dirty_count = 0                # new labels since the last write
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
        # use_classifier is the resolved bool the checks read; classifier_reason says why.
        self.use_classifier, self.classifier_reason = self._resolve_classifier_mode()

    def _resolve_classifier_mode(self):
        """README l.225-226: the evaluator is self-contained and makes no external calls inside evaluate().
        "auto" (the default) therefore only turns the model on when it cannot possibly need the network:
        a populated label cache for this model, or the model files already on disk. Nothing here imports
        transformers; that stays lazy inside TextClassifier."""
        mode = self.classifier_mode
        if mode is False:
            return False, "disabled"
        if mode is True:
            return True, "enabled"
        # setdefault: an explicit user setting wins. Must happen before transformers/huggingface_hub is
        # first imported, which is why it lives here and not next to the pipeline call.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        if local_model_present():
            return True, "local model"
        if self._labels:
            return True, "cache"
        return False, "auto: no cache and no local model"

    # ── loading ──────────────────────────────────────────────────────────────
    def load(self, accounts, owners, telemetry, artifacts, dossiers):
        self.accounts = {a["account_id"]: a for a in (accounts or [])}
        self.owners = {o["owner_id"]: o for o in (owners or [])}
        self.artifacts = {a["artifact_id"]: a for a in (artifacts or [])}
        for d in dossiers or []:
            if d.get("opened_at"):
                self.by_account[d["account_id"]].append((ts(d["opened_at"]), d.get("detector"), d.get("signal_id")))
        self.tel = self._clean_telemetry(telemetry or [])
        self.cohort_groups = self._cohort_groups()
        self.cohort_cache = {}
        if self.use_classifier:
            # README l.226: a model download (True mode) or a slow model load may happen here, never inside
            # evaluate(). Cache-only auto mode has no model to warm; a miss there is simply unverifiable.
            if self.classifier_reason != "cache":
                self._classifier_ready()
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
            # 4. bad rows: dead collector (docs/domain.md "every metric zero while status reads ok" — read as
            #    every *volume* metric zero, since a stalled collector can still report a latency) or degraded.
            #    dau_seats above contracted seats is recorded as a fact only: spec §9 M6 names no such exclusion.
            dead = api == 0 and dau == 0 and (row.get("data_volume_gb") or 0) == 0
            degraded = row.get("ingest_status") == "degraded"
            seats = acc.get("seats_contracted")
            row["usable"] = not (dead or degraded)
            row["dead"] = dead
            row["seats_overshoot"] = bool(seats) and dau > seats * spec.SEATS_OVERSHOOT_TOL
            row["bad_reason"] = "dead collector" if dead else "degraded" if degraded else None
            tel[acct][d] = row
        # 5. missing dates stay missing — never zero-filled
        return tel

    # ── cohort: leave-one-out paired change of the other accounts sharing an attribute ───────
    def _cohort_groups(self):
        groups = defaultdict(list)
        for aid, acc in self.accounts.items():
            for key in spec.COHORT_KEYS:
                if acc.get(key) is not None:
                    groups[(key, acc[key])].append(aid)
        return groups

    def cohort_change(self, metric, end, w, key, value, exclude_account):
        """(median_pct, n_accounts) of paired_change over the *other* accounts with accounts[key] == value,
        counting only accounts whose window has at least MIN_PAIRS(w) clean pairs. A move the whole cohort
        shares on the same days is a calendar or pipeline event, not one customer's behaviour."""
        k = (key, value, metric, end, w)
        if k not in self.cohort_cache:
            vals = {}
            for aid in self.cohort_groups.get((key, value), []):
                p = paired_change(self.tel.get(aid, {}), metric, end, w)
                if p["pct"] is not None and p["n_pairs"] >= spec.MIN_PAIRS(w):
                    vals[aid] = p["pct"]
            self.cohort_cache[k] = vals
        others = [v for aid, v in self.cohort_cache[k].items() if aid != exclude_account]
        return median(others), len(others)

    # ── labels ───────────────────────────────────────────────────────────────
    # Cache shape: text_key -> {"s": {sentence: score}, "n": n_chunks}. Sentences are the unit, so a
    # hypothesis change only rescores the sentences that changed, not the whole corpus.
    def _load_cache(self):
        if not (self.label_cache_path and self.label_cache_path.exists()):
            return
        try:
            data = json.loads(self.label_cache_path.read_text())
        except Exception:
            return
        meta = data.get("_meta", {})
        if meta.get("model") == NLI_MODEL and meta.get("format") == "sentences-v2":
            self._labels = {k: v for k, v in data.items() if k != "_meta"}

    def save_cache(self):
        """Write only once CACHE_FLUSH_EVERY new labels are pending — persistence is a side effect and must not
        cost a file write per dossier. Raises on I/O failure; evaluate() records that instead of surfacing it
        (README l.225-226: the result must not depend on the host filesystem)."""
        if self._dirty_count >= CACHE_FLUSH_EVERY:
            self.flush_cache()

    def flush_cache(self):
        """Force-write pending labels now (end of load_context, end of a CLI run). Raises on failure."""
        if self.label_cache_path and self._cache_dirty:
            self.label_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(self._labels)
            payload["_meta"] = {"model": NLI_MODEL, "format": "sentences-v2"}
            self.label_cache_path.write_text(json.dumps(payload))
            self._cache_dirty = False
            self._dirty_count = 0

    def _classifier_ready(self):
        if not self.use_classifier:
            return False
        if self._classifier is None:
            self._classifier = TextClassifier()
            self.classifier_active = self._classifier.available
            if not self.classifier_active:
                self.classifier_reason = f"unavailable: {self._classifier.error}"
        return self._classifier.available

    def label_text(self, text, account=None, mentions_other_account=False):
        """Label any text. Quoted / forwarded material is split off syntactically and only the head (the new
        message) is read. Cached sentence scores are reused; only missing sentences hit the model."""
        if not text or not str(text).strip():
            return {"unverifiable": True, "reason": "empty text"}
        head, tail, marker = split_quoted(text)
        quoted = bool(tail.strip())
        filled = filled_hypotheses(account)
        if not head.strip():                      # nothing but quoted material — nothing current to read
            return assemble({}, filled, NLI_THRESHOLD, mentions_other_account, 0, quoted_history=True, quote_marker=marker)
        needed = {s for ss in filled.values() for s in ss}
        key = text_key(text, account)
        entry = self._labels.get(key) or {"s": {}, "n": None}
        missing = needed - set(entry["s"])
        if missing:
            if not self._classifier_ready():
                return {"unverifiable": True, "reason": "classifier unavailable"}
            try:
                scores, n = self._classifier.score_sentences(head, missing)
            except Exception as exc:
                return {"unverifiable": True, "reason": repr(exc)}
            entry["s"].update(scores)
            entry["n"] = n
            self._labels[key] = entry
            self._cache_dirty = True
            self._dirty_count += 1
        return assemble(entry["s"], filled, NLI_THRESHOLD, mentions_other_account, entry.get("n"),
                        quoted_history=quoted, quote_marker=marker)

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
        from .checks.mandatory import departure_attributed   # same attribution rule as the per-dossier check
        trig = set()
        cust = art.get("author_type") == "customer"
        # spec §8.1 bullet 1: "from a customer-side author"
        if lab.get("cancel_intent") and cust:
            trig.add("cancel_intent")
        # spec §8.1 bullet 2 names no author — a legal reference counts whoever wrote it
        if lab.get("legal_reference"):
            trig.add("legal_reference")
        # spec §8.1 bullet 3: "raised by the customer"
        if lab.get("security_incident") and cust:
            trig.add("security_incident")
        # spec §8.1 bullet 4: only the economic buyer or named champion (author match or named in the text)
        if lab.get("departure") and departure_attributed(art, self.accounts.get(art.get("account_id"))):
            trig.add("buyer_or_champion_departure")
        if trig:
            self.account_triggers[art.get("account_id")].append((ts(art["timestamp"]), aid, trig))

    def _label_artifacts(self, dossiers):
        """Pre-warm: label the non-bot artefacts in scope (cited as evidence, or the whole corpus).
        Optional — evaluate() labels anything it meets on demand. Cache is written every CACHE_FLUSH_EVERY
        new labels and flushed at the end; an unwritable cache path is not fatal here — evaluate() reports
        it per call (README l.222: load_context is optional and must not be the thing that breaks a run)."""
        if self.label_scope == "all":
            ids = list(self.artifacts)
        else:
            ids = {ev.get("artifact_id") for d in dossiers for ev in d.get("evidence", []) or []}
        for aid in ids:
            art = self.artifacts.get(aid)
            if art and art.get("author_type") != "bot":
                self.label_artifact(art)
            try:
                self.save_cache()
            except OSError:
                pass
        try:
            self.flush_cache()
        except OSError:
            pass
        if self.classifier_active is None:
            self.classifier_active = bool(self._labels)
        self._update_coverage()

    def _update_coverage(self):
        """Does the label cache cover (nearly) every non-bot artefact in the loaded corpus?"""
        non_bot = [a for a in self.artifacts.values() if a.get("author_type") != "bot"]
        if not non_bot:
            self.labels_cover_corpus = False
            return
        def covered_(a):
            acc = self.accounts.get(a.get("account_id"))
            entry = self._labels.get(text_key(((a.get("subject") or "") + "\n" + (a.get("text") or "")).strip(), acc))
            if not entry:
                return False
            needed = {s for ss in filled_hypotheses(acc).values() for s in ss}
            return needed <= set(entry.get("s", {}))
        covered = sum(1 for a in non_bot if covered_(a))
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
