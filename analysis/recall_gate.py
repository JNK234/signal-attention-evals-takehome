"""
ABOUTME: Known-positive / known-negative gate for the NLI labels. Any hypothesis change must pass this
ABOUTME: before the corpus is relabelled. Fixtures are located by artifact id, an exact text fragment, or an inline dict.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_takehome as E  # noqa: E402
from signal_eval.context import Context  # noqa: E402

# A current cancellation notice written above a quoted, harmless old thread: the head is operative, the tail is history.
CANCEL_OVER_QUOTE = {"text": "We are cancelling at term end.\n\nOn 01 Mar 2026, X wrote:\n> all good here", "author_type": "customer"}

# ≥ 3,000 chars: a benign 2,900-char preamble, then the notice. text.MAX_BLOCK_CHARS is 1,500 — a longer block is
# scored as one truncated document and flagged; this fixture records what the model still reads past the budget.
LONG_PREAMBLE = ("Weekly usage summary: dashboards refreshed on schedule, no incidents reported, no open tickets. " * 40)[:2900]
LONG_CANCEL = {"text": LONG_PREAMBLE + "\n\nSeparately, and to be clear: we have decided not to renew and will let the "
                       "contract lapse at term end. Treat this as our formal notice.", "author_type": "customer"}
assert len(LONG_CANCEL["text"]) >= 3000

# (label, expected, locator[, "pending: <WP> — <why>"]) — locator is an artifact_id, a text fragment that identifies the
# artefact, or an inline synthetic {"text", "author_type"} dict. For label "topic", expected is a hypothesis class name
# or None. A 4th element marks an expectation the source does not meet yet: reported separately, not counted.
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
    # current notice above quoted history: cancel_intent from the head, quoted_history from the structure, and the
    # artefact is NOT stale because the operative sentence is in the head (docs/domain.md 'Quoted history')
    ("cancel_intent", True, CANCEL_OVER_QUOTE),
    ("quoted_history", True, CANCEL_OVER_QUOTE),
    ("stale", False, CANCEL_OVER_QUOTE, "pending: WP3 evidence — stale decided by where the operative text sits, not by the presence of a quote"),
    # chunking must reach the tail of a long text
    ("cancel_intent", True, LONG_CANCEL),
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
    # the corpus has no customer-raised incident that actually happened, so a synthetic positive guards the label
    ("security_incident", True, {"text": "Overnight someone used one of our API tokens from an address we do not recognise and pulled "
                                         "customer records out of the workspace. This is unauthorised access to our data and we need "
                                         "your incident report today.", "author_type": "customer"}),
    # bullet 4 — departure
    ("departure", True, "art_02820"),                                       # "Nadia Chatterjee is leaving us"
    ("departure", True, "art_00320"),                                       # "James Iyer is leaving us"
    ("departure", True, "art_01647"),                                       # "I am no longer the point of contact here"
    ("departure", False, "champion moved to a new team internally"),        # not a departure
    # spec §8.1 bullet 4 names the economic buyer or the named champion; an intern is neither
    ("departure", False, {"text": "Small update from our side: our intern resigned last week, so the onboarding tickets she filed can be closed.",
                          "author_type": "customer"}, "pending: WP2 mandatory — departure bound to named roles"),
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
    # ja-JP code-switched artefacts (docs/domain.md: ~14% of customer text code-switches)
    ("quoted_history", True, "art_01804"),                                  # 'Sorted, thanks. Ignore the thread below… On 12 Dec 2025, Aisha Lim wrote:' — structural, language-independent
    ("stale", True, "art_01804"),
    ("topic", "budget_pressure", "art_03696"),                              # 'Our budget for this line is being cut by 20%… consolidation option to the committee'
    # spec §3.2 hypothesis classes — the Q2 topic labels, one per class, plus a neutral note that names none
    ("topic", "champion_departure", {"text": "Our head of analytics, who sponsored the Cartogram rollout internally and owned the budget line, "
                                             "has resigned and leaves at the end of the month.", "author_type": "customer"}),
    ("topic", "product_gap", {"text": "We still cannot set row-level permissions on embedded dashboards. Chartroom ships this out of the box "
                                      "and the team keeps asking why we do not have it.", "author_type": "customer"}),
    ("topic", "onboarding_failure", {"text": "Six months in and the workspace is still not set up. Nobody on our side finished the rollout and "
                                             "most of the licensed seats have never logged in.", "author_type": "internal"}),
    ("topic", "reliability_erosion", {"text": "Third outage this month, and the exec dashboard timed out again in the middle of the board meeting. "
                                              "Confidence in the platform on our side is gone.", "author_type": "customer"}),
    ("topic", "benign_variation", {"text": "Heads up: the whole team is off for the national holiday week, so usage will dip until the 14th. "
                                           "Nothing to worry about, back to normal after that.", "author_type": "customer"}),
    ("topic", None, {"text": "Could you resend the calendar invite for Tuesday's sync? The link in the last email did not come through.",
                     "author_type": "customer"}),
    # held-out synthetic sentences for the three topics whose one-sentence hypotheses failed under the chosen model
    # (analysis/model_bakeoff.py). Written before the rewritten hypotheses were scored; never edited afterwards.
    # A "topic:<class>" label checks that one class's verdict, so a topic can have its own negatives.
    ("topic:product_gap", True, {"text": "We still have no way to schedule exports to SFTP. Panelworks does this natively and our ops team "
                                         "keeps asking why we cannot.", "author_type": "customer"}),
    ("topic:product_gap", True, {"text": "Missing write-back to the warehouse. We have been blocked on this for months and are looking at "
                                         "alternatives.", "author_type": "customer"}),
    ("topic:product_gap", False, {"text": "The export to SFTP ran fine last night; thanks for turning the fix around so quickly.",
                                  "author_type": "customer"}),
    ("topic:product_gap", False, {"text": "Renewal paperwork is with procurement; expect the signed order form by Friday.",
                                  "author_type": "customer"}),
    ("topic:onboarding_failure", True, {"text": "Kickoff was in January and the workspace still has no data connected; none of the licensed "
                                                "users has logged in.", "author_type": "internal"}),
    ("topic:onboarding_failure", True, {"text": "Honestly nobody on my side ever got set up. We bought the seats but the rollout never happened.",
                                        "author_type": "customer"}),
    ("topic:onboarding_failure", False, {"text": "Adoption is strong: 80 of 100 seats active weekly and the finance team built their own "
                                                 "dashboards.", "author_type": "internal"}),
    ("topic:onboarding_failure", False, {"text": "Quick one: can you add two new joiners to the marketing workspace with admin rights?",
                                         "author_type": "customer"}),
    ("topic:benign_variation", True, {"text": "We are closed for the two-week factory shutdown in August, so expect the usage numbers to fall "
                                              "until we are back.", "author_type": "customer"}),
    ("topic:benign_variation", True, {"text": "We migrated our warehouse over the weekend as planned, so the drop in API calls this week is "
                                              "expected.", "author_type": "customer"}),
    ("topic:benign_variation", False, {"text": "Usage is down because the team has stopped trusting the numbers after the refresh failures.",
                                       "author_type": "customer"}),
    ("topic:benign_variation", False, {"text": "Third outage this month; the exec dashboard timed out during the board meeting.",
                                       "author_type": "customer"}),
]


def main():
    arts = E._load("artifacts.jsonl")
    by_id = {a["artifact_id"]: a for a in arts}
    cx = Context(labeller="nli")     # default cache: analysis/.cache/labels_<model-slug>.json
    cx.accounts = {a["account_id"]: a for a in E._load("accounts.jsonl")}

    def find(loc):
        if isinstance(loc, dict):                       # inline synthetic fixture
            return {"artifact_id": "synthetic", "account_id": None, "type": "email_thread", **loc}
        if loc in by_id:
            return by_id[loc]
        # prefer an internal-authored match for the casual-note fixture, else first match
        hits = [a for a in arts if loc in (a.get("text") or "")]
        return next((a for a in hits if a.get("author_type") == "internal"), hits[0] if hits else None)

    counted, fails, pending = 0, 0, []
    for case in CASES:
        label, expected, loc = case[:3]
        pend = case[3] if len(case) > 3 else None
        a = find(loc)
        if a is None:
            print(f"  ?? fixture not found: {loc}")
            counted += 1
            fails += 1
            continue
        lab = cx.label_artifact(a)
        got = lab.get(label)
        scores = lab.get("scores") or {}
        if label == "topic":                            # show the score of the class we expected (or the one we got)
            score = scores.get(f"topic:{expected or got}") if (expected or got) else None
        elif label.startswith("topic:"):                # one class's own verdict: True / False / None (abstain)
            got, score = (lab.get("verdict") or {}).get(label), scores.get(label)
        else:
            score = scores.get(label)
        ok = (got == expected) and not lab.get("unverifiable")
        tag = "ok " if ok else ("PEND" if pend else "FAIL")
        print(f"  {tag} {label:<18} want={expected!s:<19} got={got!s:<19} score={score}  {a['artifact_id']}  {a['text'][:70]!r}")
        if pend:
            if not ok:
                pending.append((label, expected, a["artifact_id"], pend))
        else:
            counted += 1
            fails += 0 if ok else 1
    cx.flush_cache()
    print(f"\n{counted - fails}/{counted} passed")
    if pending:
        print(f"{len(pending)} pending (source fix not landed; not counted):")
        for label, expected, aid, why in pending:
            print(f"  - {label} want={expected!s} on {aid}: {why}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
