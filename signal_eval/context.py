"""
ABOUTME: The truth layer. Indexes accounts/owners/artefacts, cleans telemetry (dedup, documented
ABOUTME: event corrections, bad-row flags), computes paired-day changes and cohorts, and reads artefacts
ABOUTME: block by block through the labeller (read_artifact -> Reading) with a score cache.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import spec
from .labellers import resolve
from .labels import BOT_LABELS, EXCLUSION_LABELS, LABEL_HYPOTHESES, TOPIC_LABELS, TRIGGER_LABELS, decide, filled_hypotheses
from .text import MAX_BLOCK_CHARS, blocks, is_stale
from .util import day, median, ts


_NEVER = datetime.min.replace(tzinfo=timezone.utc)   # a row with no parseable ingested_at loses every dedup tie


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


def reading_facts(r):
    """The JSON-safe view of a Reading for _facts / saved runs: blocks as dicts, sets as sorted lists. The live
    Reading (with text.Block objects) stays internal to the checks."""
    return {"artifact_id": r.get("artifact_id"), "model_id": r.get("model_id"),
            "blocks": [{"depth": b.depth, "text": b.text, "is_signature": b.is_signature} for b in r.get("blocks", [])],
            "verdict": r.get("verdict"), "score": r.get("score"), "best": r.get("best"),
            "historical": sorted(r.get("historical", ())), "unreadable": list(r.get("unreadable", [])),
            "other_account": r.get("other_account"), "truncated": bool(r.get("truncated"))}


def other_account_name(artifact):
    """`mentions_other_account` normalised to the other account's name, or None. The data dictionary says the
    field is 'set when forwarded text names a different customer' and the corpus carries that name; a bare
    boolean (older shape) must not be mistaken for one. None / False / "" → None; True → "<unnamed>"."""
    v = (artifact or {}).get("mentions_other_account")
    if v is None or v is False or v == "":
        return None
    if not isinstance(v, str):                  # True, a list, anything non-textual: flagged but unnamed
        return "<unnamed>"
    return v.strip() or None




class Context:
    """Everything a check may consult beyond the dossier itself. Works empty (cold path) or loaded."""

    def __init__(self, *, labeller="auto", label_cache_path=None, label_scope="evidence", use_classifier=None,
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
        # use_classifier is the deprecated alias of labeller: True -> "auto", False -> None
        if use_classifier is not None:
            labeller = "auto" if use_classifier is True else None if use_classifier is False else use_classifier
        self.classifier_mode = labeller if isinstance(labeller, (str, type(None))) else "explicit"
        self.label_scope = label_scope       # "evidence" (artefacts cited by dossiers) or "all"
        self.label_cache_path = label_cache_path
        self._indexed = set()
        self.labels_cover_corpus = False
        # deployment-specific telemetry events (docs/domain.md); overridable for other deployments
        self.legacy_end = legacy_double_count_end
        self.p95_step = p95_step_date
        self.p95_factor = p95_factor
        self.ingest_gap = ingest_gap
        self.ingest_gap_regions = set(ingest_gap_regions)
        self.set_labeller(labeller)

    def set_labeller(self, labeller):
        """Resolve the engine. use_classifier is the bool the checks read; classifier_reason says why;
        classifier_active is None until a model has been tried (True for cache-only / table engines)."""
        self.labeller, self.classifier_reason = resolve(labeller, self.label_cache_path)
        self.use_classifier = self.labeller is not None
        self.classifier_active = (None if getattr(self.labeller, "available", True) is None else
                                  bool(getattr(self.labeller, "available", True))) if self.labeller else False
        self._indexed = set()
        return self.labeller

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
            # README l.226: a model download ("nli" mode) or a slow model load may happen here, never inside
            # evaluate(). Cache-only mode has no model to warm; a miss there is simply unverifiable.
            self._classifier_ready()
            self._label_artifacts(dossiers or [])
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
            if k not in latest or (ts(r.get("ingested_at")) or _NEVER) > (ts(latest[k].get("ingested_at")) or _NEVER):
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
    # The labeller is wrapped in a LabelCache keyed on (block text, hypothesis sentence, model id): a
    # hypothesis change rescores only the sentences that changed, and engines can be compared on one corpus.
    @property
    def cache(self):
        return getattr(self.labeller, "cache", None)

    def save_cache(self):
        """Write only once CACHE_FLUSH_EVERY new keys are pending — persistence is a side effect and must not
        cost a file write per dossier. Raises on I/O failure; evaluate() records that instead of surfacing it
        (README l.225-226: the result must not depend on the host filesystem)."""
        if self.cache is not None:
            self.cache.save()

    def flush_cache(self):
        """Force-write pending scores now (end of load_context, end of a CLI run). Raises on failure."""
        if self.cache is not None:
            self.cache.flush()

    def _classifier_ready(self):
        """Warm the engine (builds the NLI pipeline). classifier_active / classifier_reason record the outcome."""
        if not self.use_classifier:
            return False
        warm = getattr(self.labeller, "warm", None)
        ok = warm() if warm else True
        self.classifier_active = bool(ok)
        if not ok:
            self.classifier_reason = f"unavailable: {getattr(self.labeller, 'error', None)}"
        return bool(ok)

    def read_artifact(self, artifact, account=None):
        """Reading of one artefact: blocks (text.blocks), per-label verdict from the depth-0 content blocks
        (decide over the max score), the deciding block text, labels True only in quoted history, the blocks
        the engine could not read, the other account a forward names, and whether any block was truncated.
        Bot artefacts are system records: only the billing read (labels.BOT_LABELS). Blocks at depth ≥ 1 are
        scored too, so history is recorded rather than dropped."""
        account = account or self.accounts.get(artifact.get("account_id"))
        bs = blocks(artifact.get("subject"), artifact.get("text"))
        bot = artifact.get("author_type") == "bot"
        wanted = BOT_LABELS if bot else tuple(LABEL_HYPOTHESES)
        filled = filled_hypotheses(account, wanted)
        sentences = sorted({s for ss in filled.values() for s in ss})
        content = [i for i, b in enumerate(bs) if not b.is_signature and b.text.strip()]
        texts = [bs[i].text.strip() for i in content]
        model_id = getattr(self.labeller, "model_id", None)
        if self.labeller is not None and texts:
            res = self.labeller.label(texts, sentences)
            per_block = dict(zip(content, zip(res.scores, res.readable)))
        else:
            per_block = {i: ({}, False) for i in content}
        reading = {"artifact_id": artifact.get("artifact_id"), "blocks": bs, "model_id": model_id,
                   "verdict": {label: None for label in LABEL_HYPOTHESES}, "score": {}, "best": {}, "historical": set(),
                   "unreadable": [i for i in content if not per_block[i][1]],
                   "other_account": other_account_name(artifact),
                   "truncated": any(len(b.text) > MAX_BLOCK_CHARS for b in bs)}
        for label, ss in filled.items():
            head = [(max((per_block[i][0].get(s, 0.0) for s in ss), default=0.0), i)
                    for i in content if bs[i].depth == 0 and per_block[i][1]]
            if head:
                score, i = max(head)
                reading["score"][label] = score
                reading["best"][label] = bs[i].text.strip()
                reading["verdict"][label] = decide(score, label, model_id)
            else:
                reading["verdict"][label] = None
            if reading["verdict"][label] is not True and any(
                    decide(max((per_block[i][0].get(s, 0.0) for s in ss), default=0.0), label, model_id) is True
                    for i in content if bs[i].depth >= 1 and per_block[i][1]):
                reading["historical"].add(label)
        return reading

    def label_text(self, text, account=None, mentions_other_account=None):
        """Old-style label dict for a bare text (cold path: the dossier's quote is all we have)."""
        return self.label_artifact({"text": text, "author_type": None, "mentions_other_account": mentions_other_account},
                                   account=account)

    def label_artifact(self, artifact, account=None):
        """Old-style label dict built from read_artifact(): {unverifiable, scores, <label>: bool, topic,
        triggers_belong_elsewhere, quoted_history, historical, stale, reading}. `stale` is decided here because
        it depends on who wrote it. Every labelled artefact is also indexed by account for the unattached-trigger
        scan, so artefacts first seen inside evaluate() are covered too. Checks read this shape until WP-D
        moves them onto the Reading."""
        if not (artifact.get("text") or artifact.get("subject") or "").strip():
            return {"unverifiable": True, "reason": "empty text"}
        if self.labeller is None:
            return {"unverifiable": True, "reason": f"labeller unavailable ({self.classifier_reason})"}
        r = self.read_artifact(artifact, account)
        current = [b for b in r["blocks"] if b.depth == 0 and not b.is_signature and b.text.strip()]
        quoted = any(b.depth >= 1 for b in r["blocks"])
        if not current:                           # nothing but quoted material — nothing current to read
            lab = {"unverifiable": False, "reason": "nothing current to read", "scores": {}, "quoted_history": True}
        elif not r["score"]:                      # current text exists but no depth-0 block could be read
            return {"unverifiable": True, "reason": "unreadable", "reading": reading_facts(r)}
        else:
            lab = {"unverifiable": False, "scores": dict(r["score"]), "quoted_history": quoted}
        for lab_name in TRIGGER_LABELS + EXCLUSION_LABELS:
            lab[lab_name] = r["verdict"].get(lab_name) is True
        topics = {k[len("topic:"):]: r["score"][k] for k in TOPIC_LABELS if k in r["score"]}
        best = max(topics, key=topics.get) if topics else None
        lab["topic"] = best if best and r["verdict"].get(f"topic:{best}") is True else None
        # triggers found inside forwarded text about another account belong to that account, not this one
        lab["triggers_belong_elsewhere"] = r["other_account"] is not None
        lab["historical"] = sorted(r["historical"])
        lab["verdict"] = r["verdict"]
        lab["model_id"] = r["model_id"]
        lab["reading"] = reading_facts(r)
        lab["stale"] = is_stale(r, artifact.get("author_type"))
        self._index_triggers(artifact, lab)
        return lab

    def _index_triggers(self, art, lab):
        """Account-level index of confirmed triggers (the unattached-trigger scan in checks/mandatory). Same
        attribution rules as the per-dossier check; the renewal window is the signal's, so departures are indexed
        without it and the scan applies it per dossier. Abstains are not indexed: an uncertain read of an artefact
        the agent never attached is not evidence it missed something."""
        aid = art.get("artifact_id")
        if not aid or aid in self._indexed or lab.get("stale") or not lab.get("reading") or not art.get("timestamp"):
            return
        self._indexed.add(aid)
        from .checks.mandatory import triggers_from_reading
        t = triggers_from_reading(lab["reading"], art, self.accounts.get(art.get("account_id")), None, check_window=False)
        if t["confirmed"]:
            self.account_triggers[art.get("account_id")].append((ts(art["timestamp"]), aid, t["confirmed"]))

    def _label_artifacts(self, dossiers):
        """Pre-warm: read the artefacts in scope (cited as evidence, or the whole corpus). Optional —
        evaluate() reads anything it meets on demand. Cache is written every CACHE_FLUSH_EVERY new keys and
        flushed at the end; an unwritable cache path is not fatal here — evaluate() reports it per call
        (README l.222: load_context is optional and must not be the thing that breaks a run)."""
        if self.label_scope == "all":
            ids = list(self.artifacts)
        else:
            ids = {ev.get("artifact_id") for d in dossiers for ev in d.get("evidence", []) or []}
        for aid in ids:
            art = self.artifacts.get(aid)
            if art:
                self.label_artifact(art)
            try:
                self.save_cache()
            except OSError:
                pass
        try:
            self.flush_cache()
        except OSError:
            pass

    def _update_coverage(self):
        """Does the score cache cover (nearly) every non-bot artefact in the loaded corpus? A cache lookup per
        content block, no model call."""
        non_bot = [a for a in self.artifacts.values() if a.get("author_type") != "bot"]
        cache, model_id = self.cache, getattr(self.labeller, "model_id", None)
        if not non_bot or cache is None:
            self.labels_cover_corpus = False
            return
        def covered_(a):
            acc = self.accounts.get(a.get("account_id"))
            sentences = sorted({s for ss in filled_hypotheses(acc).values() for s in ss})
            texts = [b.text.strip() for b in blocks(a.get("subject"), a.get("text")) if not b.is_signature and b.text.strip()]
            return all(len(hit) == len(sentences) for hit in cache.get_many(texts, sentences, model_id))
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
            # an account record with an empty flag list wins over the snapshot: [] is an answer, None is not
            "flags": set(acc["flags"]) if acc is not None and acc.get("flags") is not None else set(md.get("account_flags") or []),
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
