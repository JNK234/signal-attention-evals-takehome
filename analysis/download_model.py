"""
ABOUTME: One-time, network-using download of the zero-shot NLI model into the local Hugging Face cache, so that
ABOUTME: evaluate() can read dossiers outside the committed score cache. Never called by the evaluator itself.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    # This script is the only place that is allowed to reach the network. The evaluator sets HF_HUB_OFFLINE=1
    # before it imports transformers, so the download has to happen here, before any evaluation.
    os.environ.pop("HF_HUB_OFFLINE", None)
    from signal_eval.labellers.nli import MODEL_ID, local_model_present
    if local_model_present():
        print(f"{MODEL_ID} is already in the local Hugging Face cache; nothing to do.")
        return 0
    from huggingface_hub import snapshot_download
    path = snapshot_download(MODEL_ID)
    print(f"downloaded {MODEL_ID} to {path}")
    if not local_model_present():
        print("download finished but the evaluator does not see the model; check HF_HOME", file=sys.stderr)
        return 1
    print("the evaluator will now use the local model for dossiers outside the committed cache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
