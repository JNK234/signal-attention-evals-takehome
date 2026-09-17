"""
ABOUTME: The three figures in the reports, drawn from facts.csv and the dossiers: where the complaints came from
ABOUTME: (waffle), wasted-escalation rate with vs without a rule (dumbbell), and signals per week with burst causes.
"""

import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_eval.spec import RULES  # noqa: E402

OUT = ROOT / "figures"
RED, BLUE, GREY, DARK = "#C44E52", "#4C72B0", "#CFCFCF", "#8C8C8C"
RULE_IDS = [r[0] for r in RULES]

sns.set_theme(style="ticks", context="paper", font_scale=1.1,
              rc={"font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
                  "axes.titlelocation": "left", "axes.titleweight": "bold",
                  "axes.spines.top": False, "axes.spines.right": False,
                  "axes.edgecolor": "#444444", "xtick.color": "#444444", "ytick.color": "#444444",
                  "savefig.dpi": 200, "savefig.bbox": "tight"})


def load_facts():
    with open(ROOT / "analysis" / "facts.csv") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_rules"] = {rid for rid in RULE_IDS if float(r.get(f"v_{rid}") or 0) > 0}
        r["_wasted"] = r["wasted"] == "True"
        r["_complained"] = r["complained"] == "True"
    return rows


def source(fig, text):
    fig.text(0.01, -0.04, text, fontsize=8, color="#777777")


def fig_complaints(rows):
    """One square per complaint: those on a legal_hold / mna_quiet_period account after a customer-visible play."""
    cm = [r for r in rows if r["_complained"]]
    n82 = sum("§8.2" in r["_rules"] for r in cm)
    fig, ax = plt.subplots(figsize=(7.5, 1.9))
    cols = [RED] * n82 + [GREY] * (len(cm) - n82)
    ax.scatter(range(len(cm)), [0] * len(cm), s=560, marker="s", c=cols, linewidths=0)
    ax.set_xlim(-0.7, len(cm) - 0.3)
    ax.set_ylim(-1.2, 1.6)
    ax.axis("off")
    ax.text(-0.7, 1.25, f"{n82} of {len(cm)} complaints followed a customer-visible play on a legal-hold or M&A account",
            fontsize=12, weight="bold", ha="left")
    ax.text(-0.7, 0.85, f"Each square is one complaint, n = {len(cm)}. Red: the play broke spec rule §8.2. "
                        f"Grey: other customer-visible plays.", fontsize=9, color="#555555")
    ax.text(n82 / 2 - 0.5, -0.85, "§8.2 violations: 41 dossiers", fontsize=9, color=RED, ha="center")
    ax.text(n82 + (len(cm) - n82) / 2 - 0.5, -0.85, "588 other dossiers", fontsize=9, color=DARK, ha="center")
    source(fig, "Source: outcomes.jsonl, customer_complained_about_outreach; rule findings from analysis/violations_stats.py")
    fig.savefig(OUT / "complaints.png")


def fig_wasted(rows):
    """Dumbbell: wasted-escalation rate with the rule vs without, for the rules that move it."""
    base = sum(r["_wasted"] for r in rows) / len(rows)
    label = {"§6.1": "§6.1 paged outside 08:00–19:00", "M4": "M4 routed below the materiality floor",
             "§8.6": "§8.6 wrong channel or locale", "§8.3": "§8.3 restricted artefact quoted",
             "§6.3": "§6.3 notified later than target", "§8.2": "§8.2 play on a restricted account"}
    pts = []
    for rid in label:
        w = [r for r in rows if rid in r["_rules"]]
        wo = [r for r in rows if rid not in r["_rules"]]
        pts.append((label[rid], len(w), sum(r["_wasted"] for r in w) / len(w), sum(r["_wasted"] for r in wo) / len(wo)))
    pts.sort(key=lambda p: p[2])
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    ys = range(len(pts))
    ax.hlines(list(ys), [p[3] for p in pts], [p[2] for p in pts], color="#BBBBBB", lw=2, zorder=1)
    ax.scatter([p[3] for p in pts], list(ys), s=70, color=BLUE, zorder=2)
    ax.scatter([p[2] for p in pts], list(ys), s=70, color=RED, zorder=2)
    ax.axvline(base, ls="--", lw=1, color="#888888")
    ax.text(base + 0.006, -0.75, f"corpus base rate {base:.0%}", fontsize=8, color="#666666")
    for y, (name, n, w, wo) in zip(ys, pts):
        ax.text(w + 0.012, y, f"{w:.0%}", va="center", fontsize=9, color=RED)
        ax.text(wo - 0.012, y, f"{wo:.0%}", va="center", ha="right", fontsize=9, color=BLUE)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([f"{p[0]}  (n={p[1]})" for p in pts])
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlim(0, 0.56)
    ax.text(0, 1.14, "Wasted escalations roughly double when a paging or materiality rule breaks",
            transform=ax.transAxes, fontsize=12, weight="bold")
    ax.text(0, 1.06, "Share of dossiers the owner later marked as a wasted escalation, with the rule (red) vs without it (blue)",
            transform=ax.transAxes, fontsize=9, color="#555555")
    sns.despine(ax=ax, left=True)
    ax.tick_params(left=False)
    source(fig, "Source: outcomes.jsonl, escalation_was_wasted, all 629 dossiers; rule findings from analysis/violations_stats.py")
    fig.savefig(OUT / "wasted_dumbbell.png")


def fig_weeks():
    """Signals per ISO week, with the six burst weeks labelled by cause."""
    with open(ROOT / "data" / "signal_dossiers.jsonl") as f:
        dossiers = [json.loads(l) for l in f]
    wk = Counter()
    for d in dossiers:
        y, w, _ = date.fromisoformat(d["opened_at"][:10]).isocalendar()
        wk[w] += 1
    weeks = sorted(wk)
    events = {21: ("artefact", "May 18 migration\nlegacy api_calls halved"), 24: ("artefact", "Jun 11–13 ingest gap\napac/emea rows missing"),
              14: ("holiday", "Good Friday"), 18: ("holiday", "Labour Day"), 22: ("holiday", "Memorial Day"), 27: ("holiday", "July 4")}
    col = {"artefact": RED, "holiday": DARK}
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    ax.plot(weeks, [wk[w] for w in weeks], color=BLUE, lw=2, zorder=2)
    ax.axhline(21, ls="--", lw=1, color="#888888")
    ax.text(weeks[0] + 0.1, 23, "median week: 21", fontsize=8, color="#666666")
    for w, (kind, note) in events.items():
        ax.scatter(w, wk[w], s=60, color=col[kind], zorder=3)
        dx, dy = (0, 22) if kind == "artefact" else (0, 14)
        if w == 22:
            dx, dy = (34, 18)
        ax.annotate(note, (w, wk[w]), xytext=(dx, dy), textcoords="offset points", ha="center", fontsize=8,
                    color=col[kind], arrowprops=dict(arrowstyle="-", color="#AAAAAA", lw=0.8))
    ax.set_xticks(weeks)
    ax.set_xticklabels([f"W{w}" for w in weeks], fontsize=8)
    ax.set_ylim(0, 135)
    ax.set_ylabel("signals opened per week", fontsize=9)
    ax.text(0, 1.14, "Two bursts are pipeline artefacts and four are holidays; none is customer risk at cohort level",
            transform=ax.transAxes, fontsize=12, weight="bold")
    ax.text(0, 1.06, "Dossiers by ISO week of opened_at, Feb–Jul 2026. Red: pipeline data event. Grey: national holiday.",
            transform=ax.transAxes, fontsize=9, color="#555555")
    sns.despine(ax=ax)
    ax.grid(axis="y", color="#E5E5E5", lw=0.6)
    source(fig, "Source: signal_dossiers.jsonl; causes established in attention_budget.md section 1")
    fig.savefig(OUT / "signals_per_week.png")


if __name__ == "__main__":
    rows = load_facts()
    fig_complaints(rows)
    fig_wasted(rows)
    fig_weeks()
    print("wrote", sorted(p.name for p in OUT.glob("*.png")))
