"""
ABOUTME: Corpus report for the block layer: how many artefacts carry quoted material, how many trailing
ABOUTME: signatures are tagged, the maximum quote depth, and the blocks-per-artefact histogram.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval.text import blocks  # noqa: E402


def main():
    arts = E._load("artifacts.jsonl")
    quoted, signed, closing_only, max_depth, hist, bottom = 0, 0, 0, 0, Counter(), 0
    for a in arts:
        bs = blocks(a.get("subject"), a.get("text"))
        content = [b for b in bs if not b.is_signature]
        depths = [b.depth for b in content]
        if any(d >= 1 for d in depths):
            quoted += 1
        if depths and depths[0] >= 1 and 0 in depths:
            bottom += 1
        sig = [b for b in bs if b.is_signature]
        if sig:
            signed += 1
            if len([l for l in sig[0].text.split("\n") if l.strip()]) == 1:
                closing_only += 1
        max_depth = max(max_depth, max(depths, default=0))
        hist[len(content)] += 1
    print(f"artefacts: {len(arts)}")
    print(f"with quoted material (depth ≥ 1): {quoted}   (bottom-posted: {bottom})")
    print(f"with a trailing signature: {signed}   (closing line only, no name/contact: {closing_only})")
    print(f"max depth: {max_depth}")
    print("content blocks per artefact: " + ", ".join(f"{k}: {v}" for k, v in sorted(hist.items())))


if __name__ == "__main__":
    main()
