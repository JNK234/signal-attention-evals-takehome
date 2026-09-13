"""
ABOUTME: The labeller contract every engine implements — label(texts, label_sentences) -> LabelResult — plus
ABOUTME: TableLabeller, the test double that injects meaning by substring so no model runs in unit tests.
"""

from dataclasses import dataclass
from typing import Protocol

from ..labels import label_of


@dataclass
class LabelResult:
    scores: list      # per text: {label_sentence: score}
    readable: list    # per text: False when the engine could not score it (empty, error, cache miss)


class Labeller(Protocol):
    model_id: str

    def label(self, texts, label_sentences) -> LabelResult: ...


class TableLabeller:
    """Scores from a table {substring: {label_name: score}}: a text takes the first table key it contains;
    every sentence of a label gets that label's score, anything else `default`. Texts containing a key of
    `unreadable` come back readable=False."""

    model_id = "table"
    available = True
    error = None

    def __init__(self, table, default=0.0, unreadable=()):
        self.table = dict(table)
        self.default = default
        self.unreadable = set(unreadable)

    def label(self, texts, label_sentences):
        scores, readable = [], []
        for text in texts:
            if any(k in text for k in self.unreadable):
                scores.append({})
                readable.append(False)
                continue
            row = next((v for k, v in self.table.items() if k in text), {})
            scores.append({s: float(row.get(label_of(s), self.default)) for s in label_sentences})
            readable.append(True)
        return LabelResult(scores, readable)
