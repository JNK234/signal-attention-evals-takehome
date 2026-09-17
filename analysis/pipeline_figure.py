"""
ABOUTME: Draws figures/pipeline.png, the path one dossier takes through the evaluator: setup once at the top,
ABOUTME: then sanitise, checks, violations, scores, output. Grey boxes are deterministic; amber use the text model.
"""

import sys
from pathlib import Path

import graphviz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures" / "pipeline"

GREY_FILL, GREY_LINE = "#F2F2F2", "#8C8C8C"
AMBER_FILL, AMBER_LINE = "#FBE8C8", "#C68A1E"
BLUE_FILL, BLUE_LINE = "#DCE6F2", "#4C72B0"
RED_FILL, RED_LINE = "#F4D6D6", "#C44E52"
FONT = "Helvetica"


def box(g, name, label, fill=GREY_FILL, line=GREY_LINE, width="3.2"):
    g.node(name, label, shape="box", style="rounded,filled", fillcolor=fill, color=line,
           fontname=FONT, fontsize="11", margin="0.15,0.09", width=width)


def main():
    g = graphviz.Digraph("pipeline", format="png")
    g.attr(rankdir="TB", splines="ortho", nodesep="0.6", ranksep="0.45", fontname=FONT,
           fontsize="11", dpi="200", bgcolor="white", margin="0.15")
    g.attr("edge", color="#555555", arrowsize="0.8", penwidth="1.1")

    # Setup, once. Two abstract boxes.
    with g.subgraph(name="cluster_setup") as c:
        c.attr(label="Setup, once (load_context)", fontname=FONT, fontsize="12", style="rounded",
               color="#BBBBBB", labeljust="l", margin="16")
        box(c, "rules", "Spec, read by hand\n→ 29 rules, each with its spec section\nand a severity class (spec.py)",
            fill=BLUE_FILL, line=BLUE_LINE)
        box(c, "truth", "Data files\n→ corrected telemetry + cohort baselines\n→ artefact readings: quote-depth split,\n"
                        "   zero-shot NLI scores (cached), verdicts\n→ account, owner and dossier indexes",
            fill=AMBER_FILL, line=AMBER_LINE)

    # The dossier's path.
    box(g, "dossier", "one dossier (evaluate input)", fill=BLUE_FILL, line=BLUE_LINE)
    box(g, "san", "sanitise the shape — never raise, record what was coerced")
    box(g, "struct", "structural checks (deterministic)\nlifecycle I1–I3 · transitions §4.8 · actions §5\n"
                     "timing §6.1–6.4 · materiality M1–M5 · policy §8.2–8.7")
    box(g, "text", "evidence and text checks (reader-assisted)\nevidence integrity I6 / §8.4 · staleness Q4\n"
                   "mandatory route §8.1 · hypothesis fit Q2 / I5", fill=AMBER_FILL, line=AMBER_LINE)
    box(g, "ctx", "context checks (deterministic, need the data)\ngrounding M6 on corrected telemetry · duplicates Q5")
    box(g, "vio", "violations[]  {step, rule, severity, explanation}\nseverity = class weight, halved when uncertain")
    box(g, "scores", "three scores\nquality = Π(1 − 0.16·w·c)   ·   deserved = spec-obligation table\n"
                     "risk = noisy-OR over 10 harm conditions")
    box(g, "out", "quality_score · risk_score · deserved_attention · violations",
        fill=RED_FILL, line=RED_LINE)

    g.edge("dossier", "san"); g.edge("san", "struct"); g.edge("struct", "text"); g.edge("text", "ctx")
    g.edge("ctx", "vio"); g.edge("vio", "scores"); g.edge("scores", "out")
    g.edge("rules", "struct"); g.edge("truth", "text")

    key = ('<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="6"><TR>'
           f'<TD BGCOLOR="{GREY_FILL}" BORDER="1" COLOR="{GREY_LINE}"> deterministic </TD>'
           f'<TD BGCOLOR="{AMBER_FILL}" BORDER="1" COLOR="{AMBER_LINE}"> uses the text model or its committed cache </TD>'
           f'<TD BGCOLOR="{BLUE_FILL}" BORDER="1" COLOR="{BLUE_LINE}"> input </TD>'
           f'<TD BGCOLOR="{RED_FILL}" BORDER="1" COLOR="{RED_LINE}"> the README contract </TD>'
           '</TR></TABLE>>')
    g.attr(label=key, labelloc="b", labeljust="c", fontname=FONT, fontsize="10")

    OUT.parent.mkdir(exist_ok=True)
    g.render(str(OUT), cleanup=True)
    print(f"wrote {OUT}.png")


if __name__ == "__main__":
    sys.exit(main())
