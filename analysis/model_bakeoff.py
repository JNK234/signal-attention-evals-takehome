"""
ABOUTME: Zero-shot labeller bake-off: scores the recall-gate fixtures, labels.KNOWN_CASES and a multilingual
ABOUTME: synthetic set under each candidate NLI model and reports per-label separation, AUC, thresholds and speed.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import eval_takehome as E  # noqa: E402
import recall_gate  # noqa: E402
from signal_eval.labellers.nli import NLILabeller  # noqa: E402
from signal_eval.labels import KNOWN_CASES, LABEL_HYPOTHESES, TOPIC_LABELS, filled_hypotheses  # noqa: E402
from signal_eval.text import MAX_BLOCK_CHARS, blocks  # noqa: E402

# (model_id, fallback_model_id_or_None): the fallback is tried only when the model fails to download or load.
MODELS = [
    ("MoritzLaurer/deberta-v3-base-zeroshot-v2.0", None),
    ("MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7", None),
    ("MoritzLaurer/bge-m3-zeroshot-v2.0", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"),
]

DUMMY_ACCOUNT = {"name": "Test Co", "champion": "Ada Lovelace", "economic_buyer": "Alan Turing",
                 "champion_title": "BI Manager", "economic_buyer_title": "CFO"}

# (label, expected, text) — scored as customer-authored against DUMMY_ACCOUNT.
SYNTHETIC = [
    ("cancel_intent", True, "Hallo, wir werden den Vertrag zum Laufzeitende nicht verlängern. Viele Grüße"),
    ("cancel_intent", True, "Hola, les informamos que no renovaremos el contrato este año. Saludos"),
    ("cancel_intent", True, "契約を更新しない予定です。よろしくお願いいたします。"),
    ("cancel_intent", False, "Hallo, kurze Frage zur Rechnung, danke. Viele Grüße"),
    ("legal_reference", True, "Our counsel has reviewed the SLA breach and we reserve all rights."),
    ("legal_reference", False, "Our legal team sits on floor 3, ask them for the badge."),
    ("legal_reference", False, "[jira] CART-12 transitioned to In Progress by Tomas Schmidt"),
    ("legal_reference", False, "Solid. Support response times could be better."),
    ("security_incident", True, "We found customer records exposed through the shared dashboard link — this is a data leak."),
    ("security_incident", False, "Security review scheduled for next quarter, routine."),
    ("departure", True, "Hi -- flagging that Ada Lovelace is leaving us at the end of the month."),
    ("departure", False, "We are planning a backfill of about 90M rows."),
    ("departure", False, "Leaving the history below for context only."),
    ("sarcasm", True, "great, another outage, love it 🙃 anyway not a real complaint, we know you're on it"),
    ("sarcasm", False, "QBR scheduled. no open escalations. they asked abt dark mode lol."),
    ("sarcasm", False, "We are cancelling at term end, this is formal notice."),
]


# ── fixtures ──────────────────────────────────────────────────────────────────
def _resolve(loc, arts, by_id):
    """Same rule as recall_gate.main().find: inline dict, artifact id, or a text fragment (internal author first)."""
    if isinstance(loc, dict):
        return {"artifact_id": "synthetic", "account_id": None, "type": "email_thread", **loc}
    if loc in by_id:
        return by_id[loc]
    hits = [a for a in arts if loc in (a.get("text") or "")]
    return next((a for a in hits if a.get("author_type") == "internal"), hits[0] if hits else None)


def _head_blocks(artifact):
    return [b.text.strip() for b in blocks(artifact.get("subject"), artifact.get("text"))
            if b.depth == 0 and not b.is_signature and b.text.strip()]


def load_fixtures():
    """[{source, label, expected, id, texts, sentences}] — one row per (case, label). recall_gate labels with no
    hypothesis sentences (quoted_history, stale) are structural and skipped; 'topic' cases map onto the topic:*
    labels (the named class positive, every class negative when expected is None); pending cases are skipped."""
    arts = E._load("artifacts.jsonl") or []
    by_id = {a["artifact_id"]: a for a in arts}
    accounts = {a["account_id"]: a for a in (E._load("accounts.jsonl") or [])}
    rows, skipped = [], {"structural": 0, "pending": 0, "missing": 0}

    def add(source, label, expected, ident, texts, account):
        filled = filled_hypotheses(account, [label])
        rows.append({"source": source, "label": label, "expected": bool(expected), "id": ident,
                     "texts": texts, "sentences": filled[label]})

    for case in recall_gate.CASES:
        label, expected, loc = case[:3]
        if len(case) > 3:
            skipped["pending"] += 1
            continue
        a = _resolve(loc, arts, by_id)
        if a is None:
            skipped["missing"] += 1
            print(f"  ?? fixture not found: {loc!r}")
            continue
        account = DUMMY_ACCOUNT if isinstance(loc, dict) else accounts.get(a.get("account_id"))
        ident = a["artifact_id"] if a["artifact_id"] != "synthetic" else f"inline:{(a.get('text') or '')[:40]!r}"
        texts = _head_blocks(a)
        if label == "topic":
            if expected:
                add("recall_gate", f"topic:{expected}", True, ident, texts, account)
            else:
                for t in TOPIC_LABELS:
                    add("recall_gate", t, False, ident, texts, account)
        elif label in LABEL_HYPOTHESES:
            add("recall_gate", label, expected, ident, texts, account)
        else:
            skipped["structural"] += 1
    for text, label, expected in KNOWN_CASES:
        add("known_case", label, expected, f"known:{text[:40]!r}", _head_blocks({"text": text}), DUMMY_ACCOUNT)
    for label, expected, text in SYNTHETIC:
        add("synthetic", label, expected, f"synth:{text[:40]!r}", _head_blocks({"text": text}), DUMMY_ACCOUNT)
    return rows, skipped


# ── scoring ───────────────────────────────────────────────────────────────────
def build(model_id, fallback):
    """(NLILabeller, model_id_actually_used) or (None, None) when neither loads."""
    for mid in [model_id] + ([fallback] if fallback else []):
        lab = NLILabeller(mid)
        t0 = time.time()
        if lab.warm():
            print(f"  loaded {mid} in {time.time() - t0:.1f}s on {lab.pipe.device}")
            return lab, mid
        print(f"  FAILED to load {mid}: {lab.error}")
    return None, None


def score_rows(lab, rows):
    """Fill row['score'][model_id] = max over blocks and sentences; returns (seconds, n_blocks, n_pairs)."""
    t0, n_blocks, n_pairs = time.time(), 0, 0
    for r in rows:
        if not r["texts"] or not r["sentences"]:
            r.setdefault("score", {})[lab.model_id] = None
            continue
        res = lab.label(r["texts"], r["sentences"])
        best = max((v for sc, ok in zip(res.scores, res.readable) if ok for v in sc.values()), default=None)
        r.setdefault("score", {})[lab.model_id] = best
        n_blocks += len(r["texts"])
        n_pairs += len(r["texts"]) * len(r["sentences"])
    return time.time() - t0, n_blocks, n_pairs


# ── metrics ───────────────────────────────────────────────────────────────────
def auc(pos, neg):
    """Rank-based AUC (Mann-Whitney), ties count half; None without both classes."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def best_threshold(pairs):
    """(threshold, accuracy) maximising accuracy over midpoints between neighbouring scores (>= threshold is True)."""
    vals = sorted({s for s, _ in pairs})
    cands = [0.0] + [(a + b) / 2 for a, b in zip(vals, vals[1:])] + [1.0 + 1e-9]
    best = max(cands, key=lambda t: (sum((s >= t) == e for s, e in pairs), -abs(t - 0.5)))
    return best, sum((s >= best) == e for s, e in pairs) / len(pairs)


def label_metrics(rows, model_id):
    out = {}
    for label in sorted({r["label"] for r in rows}):
        pairs = [(r["score"][model_id], r["expected"]) for r in rows if r["label"] == label and r["score"].get(model_id) is not None]
        pos = [s for s, e in pairs if e]
        neg = [s for s, e in pairs if not e]
        if not pairs:
            continue
        thr, thr_acc = best_threshold(pairs)
        # separation over the blocks that fit the model's budget: a block longer than text.MAX_BLOCK_CHARS is scored
        # truncated (text.py), so a miss there is the chunking limit, not the wording or the engine
        fit = [(r["score"][model_id], r["expected"]) for r in rows if r["label"] == label
               and r["score"].get(model_id) is not None and max(len(t) for t in r["texts"]) <= MAX_BLOCK_CHARS]
        fpos, fneg = [s for s, e in fit if e], [s for s, e in fit if not e]
        out[label] = {
            "n_pos": len(pos), "n_neg": len(neg),
            "min_pos": min(pos) if pos else None, "max_neg": max(neg) if neg else None,
            "sep": (min(pos) - max(neg)) if pos and neg else None,
            "sep_fit": (min(fpos) - max(fneg)) if fpos and fneg else None,
            "auc": auc(pos, neg),
            "acc50": sum((s >= 0.5) == e for s, e in pairs) / len(pairs),
            "thr": thr, "thr_acc": thr_acc,
        }
    return out


def _f(v, w=6):
    return f"{v:{w}.3f}" if isinstance(v, float) else f"{'-' if v is None else v!s:>{w}}"


# ── report ────────────────────────────────────────────────────────────────────
def main():
    rows, skipped = load_fixtures()
    n_blocks_total = sum(len(r["texts"]) for r in rows)
    print(f"fixtures: {len(rows)} (label, case) rows — "
          f"{sum(r['source'] == 'recall_gate' for r in rows)} recall_gate, {sum(r['source'] == 'known_case' for r in rows)} known_case, "
          f"{sum(r['source'] == 'synthetic' for r in rows)} synthetic; skipped {skipped}; {n_blocks_total} depth-0 blocks")

    timing, metrics, used = {}, {}, []
    for model_id, fallback in MODELS:
        print(f"\n== {model_id}")
        lab, mid = build(model_id, fallback)
        if lab is None:
            continue
        secs, nb, npairs = score_rows(lab, rows)
        timing[mid] = (secs, nb, npairs)
        metrics[mid] = label_metrics(rows, mid)
        used.append(mid)
        print(f"  scored {nb} blocks × sentences = {npairs} NLI pairs in {secs:.1f}s "
              f"({secs / max(nb, 1):.3f} s/block, {secs / max(npairs, 1):.4f} s/pair)")
        del lab

    if not used:
        print("no model loaded; nothing to report")
        sys.exit(1)

    for mid in used:
        print(f"\n== per-label metrics: {mid}")
        print(f"  {'label':<26}{'n+':>4}{'n-':>4}{'min_pos':>9}{'max_neg':>9}{'sep':>8}{'sep_fit':>8}{'auc':>7}{'acc@.5':>8}{'thr':>7}{'acc@thr':>9}")
        for label, m in metrics[mid].items():
            print(f"  {label:<26}{m['n_pos']:>4}{m['n_neg']:>4}{_f(m['min_pos'], 9)}{_f(m['max_neg'], 9)}{_f(m['sep'], 8)}"
                  f"{_f(m['sep_fit'], 8)}{_f(m['auc'], 7)}{m['acc50']:>8.3f}{m['thr']:>7.3f}{m['thr_acc']:>9.3f}")
    print(f"  (sep_fit: separation over fixtures whose blocks are all <= text.MAX_BLOCK_CHARS={MAX_BLOCK_CHARS}; "
          f"a longer block is scored truncated)")

    print("\n== raw scores: KNOWN_CASES + synthetic (max over depth-0 blocks × label sentences)")
    short = {mid: mid.split("/")[-1][:22] for mid in used}
    print(f"  {'label':<18}{'want':<6}" + "".join(f"{short[mid]:>24}" for mid in used) + "  text")
    for r in rows:
        if r["source"] in ("known_case", "synthetic"):
            cells = "".join(_f(r["score"].get(mid), 24) for mid in used)
            print(f"  {r['label']:<18}{r['expected']!s:<6}{cells}  {r['id'].split(':', 1)[1]}")

    print("\n== fixtures on the wrong side of each model's best threshold, and the worst-scored positive per label")
    for mid in used:
        print(f"  -- {mid}")
        for label, m in metrics[mid].items():
            lrows = [r for r in rows if r["label"] == label and r["score"].get(mid) is not None]
            for r in lrows:
                s = r["score"][mid]
                if (s >= m["thr"]) != r["expected"]:
                    print(f"     MISS {label:<20} want={r['expected']!s:<6} score={s:.3f} thr={m['thr']:.3f}  {r['id']}")
            pos = [r for r in lrows if r["expected"]]
            if pos and m["n_neg"]:
                w = min(pos, key=lambda r: r["score"][mid])
                print(f"     min+ {label:<20} score={w['score'][mid]:.3f} max-neg={m['max_neg']:.3f}  {w['id']}  "
                      f"({len(w['texts'])} blocks, longest {max(len(t) for t in w['texts'])} chars)")

    print("\n== ranking")
    rank = []
    for mid in used:
        ms = metrics[mid]
        aucs = [m["auc"] for m in ms.values() if m["auc"] is not None]
        seps = [m["sep"] for m in ms.values() if m["sep"] is not None]
        n_sep = sum(1 for s in seps if s > 0)
        mean_acc = sum(m["thr_acc"] for m in ms.values()) / len(ms)
        rank.append((n_sep, sum(aucs) / len(aucs), mean_acc, mid))
    rank.sort(reverse=True)
    for i, (n_sep, mean_auc, mean_acc, mid) in enumerate(rank, 1):
        secs, nb, _ = timing[mid]
        print(f"  {i}. {mid}: labels separated {n_sep}/{len(metrics[mid])}, mean AUC {mean_auc:.3f}, "
              f"mean acc@best-thr {mean_acc:.3f}, {secs / max(nb, 1):.3f} s/block")

    # hypothesis-vs-model diagnosis on the blocks that fit the budget: a label no model separates points at the
    # wording; one only some separate, at the engine; a label separated only once truncated blocks are excluded
    # points at chunking (text.MAX_BLOCK_CHARS), not at either.
    labels = sorted({l for mid in used for l in metrics[mid]})
    nobody, everybody, some, truncated_only = [], [], [], []
    for label in labels:
        ms = [metrics[mid][label] for mid in used if label in metrics[mid]]
        seps = [m["sep_fit"] for m in ms if m["sep_fit"] is not None]
        if not seps:
            continue
        if all(m["sep"] is not None and m["sep"] <= 0 < (m["sep_fit"] or 0) for m in ms):
            truncated_only.append(label)
        (everybody if all(s > 0 for s in seps) else nobody if all(s <= 0 for s in seps) else some).append(label)
    winner = rank[0][3]
    weak = [l for l, m in metrics[winner].items() if m["sep_fit"] is not None and m["sep_fit"] <= 0]
    thin = [l for l, m in metrics[winner].items() if m["sep_fit"] is not None and 0 < m["sep_fit"] < 0.05]
    cut = [l for l, m in metrics[winner].items() if m["sep"] is not None and m["sep"] <= 0 < (m["sep_fit"] or 0)]
    print("\n== recommendation")
    print(f"  Use {winner}: it separates the most labels on this fixture set (see ranking). On blocks within the "
          f"budget its non-separated labels are {weak or 'none'}, its margins under 0.05 are {thin or 'none'}, and "
          f"{cut or 'no label'} fail{'s' if len(cut) == 1 else ''} only on a block longer than MAX_BLOCK_CHARS "
          f"(the chunking limit, not the wording). Every model separates {everybody or 'no label'}; no model "
          f"separates {nobody or 'no label'} — there the hypothesis wording, not the engine, is the first suspect; "
          f"{some or 'no label'} split the models, so there the engine matters"
          + (f"; {truncated_only} fail only past the block budget under every model." if truncated_only else "."))


if __name__ == "__main__":
    main()
