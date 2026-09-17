"""
ABOUTME: Figures for violations.md, drawn from the same facts.csv the statistics script reads: rule frequency by
ABOUTME: severity class, outcome lift per rule, rule rate by detector, and critical-finding share by owner.
"""

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_eval.spec import RULES, RULE_SEVERITY  # noqa: E402

OUT = ROOT / "figures"
BAD = {"churned", "downgraded"}
CLASS_COLOR = {"critical": "#b2182b", "high": "#ef8a62", "medium": "#fddbc7", "soft": "#999999"}
RULE_IDS = [r[0] for r in RULES]


def load():
    with open(ROOT / "analysis" / "facts.csv") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_rules"] = {rid for rid in RULE_IDS if float(r.get(f"v_{rid}") or 0) > 0}
        r["_known"] = r["renewal_outcome"] not in ("", "pending", "None")
        r["_bad"] = r["renewal_outcome"] in BAD
        r["_wasted"] = r["wasted"] == "True"
        r["_complained"] = r["complained"] == "True"
        r["_routed"] = r["disposition"] in ("routed", "acknowledged")
        r["_critical"] = any(RULE_SEVERITY[x] == "critical" for x in r["_rules"])
    return rows


def fig_frequency(rows):
    counts = [(rid, sum(rid in r["_rules"] for r in rows)) for rid in RULE_IDS]
    counts = [c for c in counts if c[1] > 0]
    counts.sort(key=lambda x: x[1])
    fig, ax = plt.subplots(figsize=(8, 7))
    ax.barh([c[0] for c in counts], [c[1] / len(rows) * 100 for c in counts],
            color=[CLASS_COLOR[RULE_SEVERITY[c[0]]] for c in counts])
    for i, (rid, n) in enumerate(counts):
        ax.text(n / len(rows) * 100 + 0.5, i, f"{n}", va="center", fontsize=8)
    ax.set_xlabel("share of 629 dossiers with at least one finding (%)")
    ax.set_title("Most findings are reasoning and grounding; the critical rules are rarer", fontsize=11)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=col, label=cls) for cls, col in CLASS_COLOR.items()],
              title="spec severity class", loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "violations_frequency.png", dpi=150)


def fig_lift(rows):
    base_c = sum(r["_complained"] for r in rows) / len(rows)
    base_w = sum(r["_wasted"] for r in rows) / len(rows)
    known = [r for r in rows if r["_known"]]
    base_b = sum(r["_bad"] for r in known) / len(known)
    pts = []
    for rid in RULE_IDS:
        w = [r for r in rows if rid in r["_rules"]]
        if len(w) < 15 or sum(r["_routed"] for r in w) == 0:
            continue
        c = sum(r["_complained"] for r in w) / len(w) / base_c
        ww = sum(r["_wasted"] for r in w) / len(w) / base_w
        kb = [r for r in w if r["_known"]]
        b = (sum(r["_bad"] for r in kb) / len(kb) / base_b) if kb else 0
        pts.append((rid, len(w), c, ww, b))
    pts.sort(key=lambda p: -(p[2] + p[3] + p[4]))
    fig, ax = plt.subplots(figsize=(9, 6))
    y = range(len(pts))
    ax.scatter([p[2] for p in pts], y, s=[p[1] for p in pts], color="#b2182b", alpha=0.7, label="complaint")
    ax.scatter([p[3] for p in pts], y, s=[p[1] for p in pts], color="#ef8a62", alpha=0.7, label="wasted escalation")
    ax.scatter([p[4] for p in pts], y, s=[p[1] for p in pts], color="#4393c3", alpha=0.7, label="churn or downgrade")
    ax.axvline(1.0, color="black", lw=0.8, ls="--")
    ax.set_yticks(list(y))
    ax.set_yticklabels([f"{p[0]}  (n={p[1]})" for p in pts])
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("outcome rate with the rule ÷ base rate (log scale; 1 = no association)")
    ax.set_title("One rule drives complaints; timing and materiality rules drive waste; churn barely moves", fontsize=11)
    ax.legend(loc="lower right", fontsize=8, title="marker size = dossiers")
    fig.tight_layout()
    fig.savefig(OUT / "violations_lift.png", dpi=150)


def fig_detector(rows):
    dets = [d for d, _ in Counter(r["detector"] for r in rows).most_common()]
    show = []
    for rid in RULE_IDS:
        per = []
        for d in dets:
            rs = [r for r in rows if r["detector"] == d]
            per.append(sum(rid in r["_rules"] for r in rs) / len(rs))
        if max(per) - min(per) >= 0.15:
            show.append((rid, per))
    fig, ax = plt.subplots(figsize=(9, 5.5))
    im = ax.imshow([[p * 100 for p in per] for _, per in show], cmap="Reds", aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(len(dets)))
    ax.set_xticklabels([f"{d}\n(n={sum(r['detector']==d for r in rows)})" for d in dets], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(show)))
    ax.set_yticklabels([s[0] for s in show])
    for i, (_, per) in enumerate(show):
        for j, p in enumerate(per):
            ax.text(j, i, f"{p*100:.0f}", ha="center", va="center", fontsize=8, color="white" if p > 0.5 else "black")
    plt.colorbar(im, ax=ax, label="share of the detector's dossiers with the finding (%)")
    ax.set_title("Grounding failures live on the telemetry detectors; containment failures on security reviews", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "violations_by_detector.png", dpi=150)


def fig_groups(rows):
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5), gridspec_kw={"width_ratios": [1, 1, 2.2]})
    for ax, key, title in zip(axes, ("tier", "region", "owner_id"), ("account tier", "region", "owner")):
        groups = defaultdict(list)
        for r in rows:
            groups[r[key] or "-"].append(r)
        items = sorted(groups.items(), key=lambda kv: -sum(x["_critical"] for x in kv[1]) / len(kv[1]))
        if key == "owner_id":
            items = [kv for kv in items if len(kv[1]) >= 20]
        ax.bar([g for g, _ in items], [sum(x["_critical"] for x in rs) / len(rs) * 100 for _, rs in items], color="#b2182b", alpha=0.8)
        for i, (g, rs) in enumerate(items):
            ax.text(i, sum(x["_critical"] for x in rs) / len(rs) * 100 + 1, f"n={len(rs)}", ha="center", fontsize=7)
        ax.set_ylim(0, 80)
        ax.set_title(f"by {title}", fontsize=10)
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    axes[0].set_ylabel("dossiers with a critical finding (%)")
    fig.suptitle("Tier and region are flat; owner is where the critical rate varies", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "violations_by_group.png", dpi=150)


if __name__ == "__main__":
    rows = load()
    fig_frequency(rows)
    fig_lift(rows)
    fig_detector(rows)
    fig_groups(rows)
    print("wrote", sorted(p.name for p in OUT.glob("violations_*.png")))
