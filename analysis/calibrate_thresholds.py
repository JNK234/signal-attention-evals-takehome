"""
ABOUTME: Sets each label's threshold band on the calibration half of the recall-gate fixtures and reports the
ABOUTME: held-out confusion under it. The table is pasted into labels.THRESHOLDS by hand (reviewed diff), never written.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import eval_takehome as E  # noqa: E402
import recall_gate  # noqa: E402
from signal_eval.context import Context  # noqa: E402
from signal_eval.labels import DEFAULT_BAND, LABEL_HYPOTHESES  # noqa: E402

EPS = 0.01          # margin past the extreme calibration score on each side
HALF_BAND = 0.05    # abstain half-width around the gap midpoint when the classes separate
MIN_PER_CLASS = 2   # fewer positives or negatives than this → uncalibrated, keep DEFAULT_BAND


def propose(rows):
    """{label: {band, flag, n_pos, n_neg, min_pos, max_neg}} from the calibration rows of every label. high =
    max(neg) + ε, low = min(pos) − ε; when low ≥ high the True and False regions overlap (the classes separate)
    and the band collapses to the gap midpoint ± HALF_BAND, flagged "overlapping"; when low < high the classes
    overlap and everything between abstains ("classes overlap"). Bands are clipped to [0, 1].
    Guard (stated without this corpus): calibration may narrow or shift the band inside the model's uncertain
    region, but a True verdict still needs a score ≥ DEFAULT_BAND[0] and a False verdict a score ≤ DEFAULT_BAND[1].
    Two fixtures with a positive at the noise floor would otherwise set high ≈ 0.01 and call True on noise; the
    guard turns that into an abstain, flagged "guarded". `raw` keeps the unguarded band for the report."""
    out = {}
    for label in LABEL_HYPOTHESES:
        pos = [r["score"] for r in rows if r["label"] == label and r["expected"] is True]
        neg = [r["score"] for r in rows if r["label"] == label and r["expected"] is False]
        info = {"n_pos": len(pos), "n_neg": len(neg), "min_pos": min(pos) if pos else None, "max_neg": max(neg) if neg else None}
        if len(pos) < MIN_PER_CLASS or len(neg) < MIN_PER_CLASS:
            out[label] = dict(info, band=DEFAULT_BAND, flag="uncalibrated: n too small")
            continue
        high, low = max(neg) + EPS, min(pos) - EPS
        if low >= high:
            mid = (max(neg) + min(pos)) / 2
            low, high, flag = mid - HALF_BAND, mid + HALF_BAND, "overlapping"
        else:
            flag = "classes overlap"
        raw = (round(max(low, 0.0), 3), round(min(high, 1.0), 3))
        band = (min(raw[0], DEFAULT_BAND[1]), max(raw[1], DEFAULT_BAND[0]))
        if band != raw:
            flag += "; guarded"
        out[label] = dict(info, band=band, raw=raw, flag=flag)
    return out


def verdict(score, band):
    low, high = band
    return True if score >= high else False if score <= low else None


def confusion(rows, bands):
    """Per label: Counter over (expected, got) with got in True / False / None, on the given rows."""
    out = {}
    for r in rows:
        band = bands.get(r["label"], {}).get("band", DEFAULT_BAND)
        out.setdefault(r["label"], Counter())[(r["expected"], verdict(r["score"], band))] += 1
    return out


def scored_rows(cases, split, counted=True):
    """Rows with a score and a True/False expectation on one split; counted=False gives the 'limit'/'pending' rows."""
    return [r for r in cases if r["split"] == split and r["score"] is not None and r["expected"] in (True, False)
            and (r["outcome"] != "pending") == counted and r["outcome"] != "missing"]


def _f(v):
    return "-" if v is None else f"{v:.3f}"


def main():
    arts = E._load("artifacts.jsonl")
    cx = Context(labeller="nli")
    cx.accounts = {a["account_id"]: a for a in E._load("accounts.jsonl")}
    cases = recall_gate.score_cases(cx, recall_gate.load_cases(arts))
    calib, held = scored_rows(cases, "calib"), scored_rows(cases, "heldout")
    bands = propose(calib)

    print(f"model {cx.labeller.model_id}: {len(calib)} calibration rows, {len(held)} held-out rows "
          f"({recall_gate.shared_text_count(cases)} of {len(cases)} fixtures share a text with another; same side by construction)\n")
    print("== proposed THRESHOLDS (set on the calibration half only)")
    print(f"  {'label':<26}{'n+':>4}{'n-':>4}{'min_pos':>9}{'max_neg':>9}{'raw low':>9}{'raw high':>9}{'low':>7}{'high':>7}  flag")
    for label, b in bands.items():
        raw = b.get("raw", b["band"])
        print(f"  {label:<26}{b['n_pos']:>4}{b['n_neg']:>4}{_f(b['min_pos']):>9}{_f(b['max_neg']):>9}{raw[0]:>9.3f}{raw[1]:>9.3f}"
              f"{b['band'][0]:>7.3f}{b['band'][1]:>7.3f}  {b['flag']}")
    print("  (overlapping: the classes separate on calib, so the True/False regions overlap; band = gap midpoint ± "
          f"{HALF_BAND}. classes overlap: band spans the overlap, everything inside abstains. guarded: True needs "
          f"score ≥ {DEFAULT_BAND[0]}, False needs score ≤ {DEFAULT_BAND[1]} — see propose().)")

    print("\n== held-out confusion under the proposed bands (expected × got)")
    print(f"  {'label':<26}{'n':>4}{'TP':>4}{'FN':>4}{'P?':>4}{'TN':>4}{'FP':>4}{'N?':>4}   errors")
    conf = confusion(held, bands)
    tot = Counter()
    for label in LABEL_HYPOTHESES:
        c = conf.get(label)
        if not c:
            continue
        cells = [c[(True, True)], c[(True, False)], c[(True, None)], c[(False, False)], c[(False, True)], c[(False, None)]]
        tot.update(dict(zip(("TP", "FN", "P?", "TN", "FP", "N?"), cells)))
        errs = [r["id"] for r in held if r["label"] == label and verdict(r["score"], bands[label]["band"]) not in (None, r["expected"])]
        print(f"  {label:<26}{sum(c.values()):>4}" + "".join(f"{v:>4}" for v in cells) + f"   {', '.join(errs)}")
    n = sum(tot.values())
    abst = tot["P?"] + tot["N?"]
    print(f"  {'total':<26}{n:>4}" + "".join(f"{tot[k]:>4}" for k in ("TP", "FN", "P?", "TN", "FP", "N?")))
    print(f"  held-out: {tot['TP'] + tot['TN']}/{n} correct, {tot['FN'] + tot['FP']} errors, "
          f"{abst} abstain ({abst / n:.1%} abstain rate)" if n else "  no held-out rows")

    limits = scored_rows(cases, "heldout", counted=False) + scored_rows(cases, "calib", counted=False)
    if limits:
        print("\n== 'limit' / 'pending' rows under the proposed bands (reported, not counted)")
        for r in limits:
            print(f"  {r['split']:<7} {r['label']:<26} want={r['expected']!s:<6} got={verdict(r['score'], bands[r['label']]['band'])!s:<6} "
                  f"score={r['score']:.3f}  {r['id']}: {r['note']}")

    print("\n== paste into labels.THRESHOLDS[MODEL_ID]")
    for label, b in bands.items():
        print(f'    "{label}": ({b["band"][0]:.3f}, {b["band"][1]:.3f}),{"" if b["flag"] == "overlapping" else "   # " + b["flag"]}')


if __name__ == "__main__":
    main()
