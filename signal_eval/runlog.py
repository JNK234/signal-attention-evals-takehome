"""
ABOUTME: Run persistence — every full-corpus evaluation is saved as one JSONL file (meta line, then one
ABOUTME: line per dossier with result + facts) so runs can be diffed and re-analysed without re-evaluating.
"""

import dataclasses
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _jsonable(o):
    """json.dumps default: dataclasses (text.Block) become dicts, sets become sorted lists."""
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def git_sha():
    """Short SHA of HEAD, or "unknown". Only ever called from analysis scripts, never from evaluate()."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def save_run(path, rows, meta):
    """Write line 0 `{"_meta": {created, git_sha, model_id, classifier_reason, thresholds, n}}`, then one line
    per dossier `{signal_id, result, facts}`. `meta` supplies model_id / classifier_reason / thresholds; the rest
    is filled here. Returns the meta dict as written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    full = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "model_id": meta.get("model_id"),
        "classifier_reason": meta.get("classifier_reason"),
        "thresholds": meta.get("thresholds"),
        "n": len(rows),
    }
    full.update({k: v for k, v in meta.items() if k not in full})
    with open(path, "w") as f:
        f.write(json.dumps({"_meta": full}, default=_jsonable) + "\n")
        for row in rows:
            f.write(json.dumps(row, default=_jsonable) + "\n")
    return full


def load_run(path):
    """(meta, rows) from a file written by save_run."""
    with open(path) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    if not lines or "_meta" not in lines[0]:
        raise ValueError(f"{path}: first line is not a _meta record")
    return lines[0]["_meta"], lines[1:]
