"""
ABOUTME: Known-positive / known-negative gate for the NLI labels. Any hypothesis change must pass this
ABOUTME: before the corpus is relabelled. Fixtures are located by artifact id or an exact text fragment.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval.context import Context  # noqa: E402

# (label, expected, locator) — locator is an artifact_id or a text fragment that identifies the artefact
CASES = [
    # spec §8.1 bullet 1 — cancel intent
    ("cancel_intent", True, "art_02682"),                                   # "Treat this thread as formal notice"
    ("cancel_intent", True, "art_01299"),
    ("cancel_intent", True, "art_01374"),                                   # "Notice of intent not to renew"
    ("cancel_intent", True, "art_02877"),
    ("cancel_intent", True, "art_00206"),
    ("cancel_intent", True, "art_03362"),
    ("cancel_intent", False, "art_00620"),                                  # rate-limit question
    ("cancel_intent", False, "art_03177"),                                  # routine check-in
    ("cancel_intent", False, "art_02546"),                                  # positive reply over old complaint
    # bullet 2 — legal
    ("legal_reference", True, "art_02612"),                                 # counsel / material breach
    ("legal_reference", True, "art_02298"),                                 # legal team / DPA
    ("legal_reference", False, "art_00620"),
    ("legal_reference", False, "art_01637"),                                # incident review asking for a credit — not legal
    # spec §8.1 names "a regulatory body"; the corpus has no such artefact, so a synthetic positive guards it
    ("legal_reference", True, {"text": "We have escalated this to the ICO and expect the data protection authority to open an inquiry.", "author_type": "customer"}),
    # bullet 3 — security incident (customer-raised, actually occurred)
    ("security_incident", False, "art_00017"),                              # SSO feature request
    ("security_incident", False, "row-level security came up again"),       # feature request
    ("security_incident", False, "art_02564"),                              # internal pen-test follow-up (not customer-raised)
    # bullet 4 — departure
    ("departure", True, "art_02820"),                                       # "Nadia Chatterjee is leaving us"
    ("departure", True, "art_00320"),                                       # "James Iyer is leaving us"
    ("departure", True, "art_01647"),                                       # "I am no longer the point of contact here"
    ("departure", False, "champion moved to a new team internally"),        # not a departure
    # bullet 5 — billing
    ("billing_dispute", True, "Status: disputed by customer AP"),
    ("billing_dispute", False, "Status: paid"),
    # exclusions (docs/domain.md)
    ("quoted_history", True, "art_02546"),
    ("quoted_history", True, "art_00245"),
    ("quoted_history", False, "art_02682"),
    ("quoted_history", False, "art_03224"),                                 # credit memo — positive tone, nothing quoted
    ("quoted_history", False, "art_03219"),                                 # meetup email — positive, nothing quoted
    ("quoted_history", False, "art_00225"),                                 # QBR meeting note
    ("quoted_history", False, "art_01637"),                                 # incident-review note with a quoted remark, no email thread
    ("quoted_history", True, "art_01536"),                                  # "Sorted, thanks... On 07 Mar 2026, X wrote:"
    ("sarcasm", True, "art_03476"),                                         # "love it 🙃 ... not a real complaint"
    ("sarcasm", False, "art_02682"),
    # an internal note with "lol" may read as a joke, but it is not stale customer evidence to discount
    ("stale", False, "QBR scheduled. no open escalations. they asked abt dark mode lol."),
    ("stale", True, "art_02546"),
]


def main():
    arts = E._load("artifacts.jsonl")
    by_id = {a["artifact_id"]: a for a in arts}
    cx = Context(label_cache_path=Path("analysis/.cache/labels.json"))
    cx.accounts = {a["account_id"]: a for a in E._load("accounts.jsonl")}

    def find(loc):
        if isinstance(loc, dict):                       # inline synthetic fixture
            return {"artifact_id": "synthetic", "account_id": None, "type": "email_thread", **loc}
        if loc in by_id:
            return by_id[loc]
        # prefer an internal-authored match for the casual-note fixture, else first match
        hits = [a for a in arts if loc in (a.get("text") or "")]
        return next((a for a in hits if a.get("author_type") == "internal"), hits[0] if hits else None)

    fails = 0
    for label, expected, loc in CASES:
        a = find(loc)
        if a is None:
            print(f"  ?? fixture not found: {loc}")
            fails += 1
            continue
        lab = cx.label_artifact(a)
        got = lab.get(label)
        score = (lab.get("scores") or {}).get(label)
        ok = (got == expected) and not lab.get("unverifiable")
        fails += 0 if ok else 1
        print(f"  {'ok ' if ok else 'FAIL'} {label:<18} want={expected!s:<5} got={got!s:<5} score={score}  {a['artifact_id']}  {a['text'][:70]!r}")
    cx.save_cache()
    print(f"\n{len(CASES)-fails}/{len(CASES)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
