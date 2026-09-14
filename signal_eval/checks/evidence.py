"""
ABOUTME: Evidence integrity — spec §7 I6 (exists, same account, verbatim) and §8.4 P4.
ABOUTME: Labels each entry with the model when possible: from the artefact if the corpus is loaded,
ABOUTME: from the bare quote if not — so the cold path still reads.
"""

from ..spec import violation
from ..text import blocks, find_quote, is_stale
from ..util import author_class, norm, ts


def quote_location(quote, subject, text):
    """Where a verbatim quote sits in the artefact: "head" (a current depth-0 block), "tail" (quoted / forwarded
    history at depth ≥ 1, per text.blocks), "both", or None when empty or spanning the boundary.
    docs/domain.md 'Quoted history': only the tail repeats the old complaint; the head is current."""
    return find_quote(quote, blocks(subject, text))


def check_evidence(d, ctx, cx):
    """Runs on both paths. With corpus: I6/P4 + labels from full artefact text. Without: labels from quotes."""
    out = []
    verified, stale, facts = [], [], []
    opened = ts(d.get("opened_at"))
    acct = cx.accounts.get(d.get("account_id")) if cx.loaded else None
    for ev in d.get("evidence") or []:
        aid = ev.get("artifact_id")
        q = ev.get("quote") or ""
        f = {"artifact_id": aid, "step": ev.get("step"), "status": None, "label_source": None, "quote_location": None}
        facts.append(f)

        if not cx.loaded:
            # cold path: the quote is all we have. Label it, mark provenance, count it as attached text.
            lab = cx.label_text(q, acct) if q else {"unverifiable": True, "reason": "empty quote"}
            if not lab.get("unverifiable"):
                lab = dict(lab, stale=is_stale(lab.get("reading"), None))
            f.update(status="unverified", label_source="quote", labels=lab, author_type=None, type=ev.get("source"),
                     restricted=ev.get("restricted"))
            pseudo = {"artifact_id": aid, "source": ev.get("source"), "type": ev.get("source"), "author_type": None,
                      "restricted": ev.get("restricted"), "text": q, "_label": lab}
            if lab.get("stale"):
                stale.append(pseudo)
            else:
                verified.append(pseudo)
            continue

        art = cx.artifacts.get(aid)
        if art is None:
            f["status"] = "missing"
            out.append(violation(ev.get("step"), "I6", f"{aid} does not exist in the artefact corpus"))
            continue
        art_ts = ts(art.get("timestamp"))
        f.update(author_type=art.get("author_type"), type=art.get("type"), restricted=art.get("restricted"),
                 age_days=abs((opened - art_ts).days) if opened and art_ts else None,
                 mentions_other=bool(art.get("mentions_other_account")))
        if art.get("account_id") != d.get("account_id"):
            f["status"] = "other_account"
            out.append(violation(ev.get("step"), "P4", f"{aid} belongs to {art.get('account_id')}, signal is on {d.get('account_id')} (also fails I6 same-account test)"))
            continue
        hay = (art.get("subject") or "") + "\n" + (art.get("text") or "")
        if q and q not in hay:
            if norm(q) in norm(hay):
                # spec §7 I6 says "verbatim": a near match is a diagnostic, not verified evidence
                f["status"] = "near_verbatim"
                out.append(violation(ev.get("step"), "I6", f"quote from {aid} matches only after whitespace/punctuation normalisation", certain=False))
            else:
                f["status"] = "fabricated"
                out.append(violation(ev.get("step"), "I6", f"quote not found in {aid}: \"{q[:80]}\""))
            continue
        # verbatim and same-account. Is the content current, or quoted history / a joke?
        # Currency is decided by where the quote sits (docs/domain.md 'Quoted history'), then by the Reading's
        # depth-0 sarcasm verdict for a customer author.
        loc = quote_location(q, art.get("subject"), art.get("text"))
        lab = cx.label_artifact(art) if cx.use_classifier else {"unverifiable": True}
        lab = dict(lab, stale=is_stale(lab.get("reading"), art.get("author_type"), quote_in_tail=loc == "tail"))
        f.update(labels=lab, label_source="artifact", quote_location=loc)
        art = dict(art, _label=lab)
        if lab["stale"]:
            f["status"] = "stale"
            stale.append(art)
            why = "quoted from the forwarded / quoted history below the current message" if loc == "tail" else "sarcasm"
            out.append(violation(ev.get("step"), "Q4", f"{aid} is {why}; spec §4.2 says stay in state rather than build on it", certain=False))
            continue
        f["status"] = "verified"
        verified.append(art)
    ctx.update(
        verified=verified, stale=stale, evidence_facts=facts,
        verified_sources={a.get("source") for a in verified if a.get("source") != "bot_alert"},
        has_customer_text=any(author_class(a.get("author_type")) == "customer" for a in verified),
        has_attached_text=bool(verified),
    )
    return out
