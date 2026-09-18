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

    # Setup, once. Two boxes, few words.
    with g.subgraph(name="cluster_setup") as c:
        c.attr(label="Setup, once", fontname=FONT, fontsize="12", style="rounded",
               color="#BBBBBB", labeljust="l", margin="16")
        box(c, "rules", "Spec\n→ 29 rules with severity", fill=BLUE_FILL, line=BLUE_LINE, width="2.4")
        box(c, "truth", "Data files\n→ corrected telemetry\n→ artefact readings (NLI)", fill=AMBER_FILL, line=AMBER_LINE, width="2.6")

    # The dossier's path.
    box(g, "dossier", "one dossier", fill=BLUE_FILL, line=BLUE_LINE, width="2.4")
    box(g, "san", "sanitise\nnever raise", width="2.4")
    box(g, "struct", "structural checks\nlifecycle · timing · materiality · policy", width="3.4")
    box(g, "text", "text checks\nevidence · mandatory route · hypothesis fit",
        fill=AMBER_FILL, line=AMBER_LINE, width="3.4")
    box(g, "ctx", "data checks\ngrounding M6 · duplicates Q5", width="3.4")
    box(g, "vio", "violations\nstep · rule · severity · explanation", width="3.4")
    box(g, "scores", "scores\nquality · risk · deserved", width="3.4")
    box(g, "out", "quality_score · risk_score\ndeserved_attention · violations",
        fill=RED_FILL, line=RED_LINE, width="3.4")

    g.edge("dossier", "san"); g.edge("san", "struct"); g.edge("struct", "text"); g.edge("text", "ctx")
    g.edge("ctx", "vio"); g.edge("vio", "scores"); g.edge("scores", "out")
    g.edge("rules", "struct"); g.edge("truth", "text"); g.edge("truth", "ctx")

    key = ('<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="6"><TR>'
           f'<TD BGCOLOR="{GREY_FILL}" BORDER="1" COLOR="{GREY_LINE}"> deterministic </TD>'
           f'<TD BGCOLOR="{AMBER_FILL}" BORDER="1" COLOR="{AMBER_LINE}"> uses the text model or its committed cache </TD>'
           f'<TD BGCOLOR="{BLUE_FILL}" BORDER="1" COLOR="{BLUE_LINE}"> input </TD>'
           f'<TD BGCOLOR="{RED_FILL}" BORDER="1" COLOR="{RED_LINE}"> output </TD>'
           '</TR></TABLE>>')
    g.attr(label=key, labelloc="b", labeljust="c", fontname=FONT, fontsize="10")

    OUT.parent.mkdir(exist_ok=True)
    g.render(str(OUT), cleanup=True)
    print(f"wrote {OUT}.png")


if __name__ == "__main__":
    sys.exit(main())
