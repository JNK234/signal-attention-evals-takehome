"""
ABOUTME: Policy checks that need only the dossier — spec §8.2 contact restrictions,
ABOUTME: §8.3 restricted-source containment, §8.5 confidence honesty, §8.7 contact details.
"""

from ..spec import CUSTOMER_VISIBLE_PLAYS, HIGH_CONF_MIN_SOURCES, RESTRICT_FLAGS, violation
from ..util import EMAIL_RE, first_hypothesis, has_phone, norm, reached_human


def check_policy(d, ctx, cx):
    out = []
    af = ctx["acct"]
    dec = d.get("decision") or {}
    human = reached_human(d)                       # "routed to the account team" = a human was notified
    play = dec.get("recommended_play")
    visible = bool(dec.get("customer_visible")) or play in CUSTOMER_VISIBLE_PLAYS
    step = next((e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") in ("routed", "suppressed")), 0)
    # §8.2 — legal_hold / mna_quiet_period: internal routing only
    hit = af["flags"] & RESTRICT_FLAGS
    if hit and visible:
        out.append(violation(step, "§8.2", f"customer-visible play {play} on account flagged {sorted(hit)}"))
    for ev in d.get("evidence") or []:
        q = ev.get("quote") or ""
        # §8.3 — spec §8.3: "must not quote restricted material verbatim in a dossier that is routed to the
        # account team". With the corpus loaded the artefact record decides `restricted` and the quote must
        # actually appear verbatim in it (a quote that is not there is I6's fabrication, not a leak); on the
        # cold path the dossier's own flag is all we have. A dossier no human was put on is not "routed to the
        # account team": the quote is recorded as a fact, not a violation.
        if cx.loaded:
            art = cx.artifacts.get(ev.get("artifact_id"))
            hay = ((art.get("subject") or "") + "\n" + (art.get("text") or "")) if art is not None else ""
            # The harm §8.3 names is that the dossier "is read by people who are not cleared for it", so the
            # test is whether the restricted content reached the reader — not whether it is byte-exact. A
            # quote that matches only after normalisation still exposes it, and check_evidence already calls
            # that case near_verbatim rather than fabricated. A quote that matches neither way is invented,
            # which is I6's finding and exposes nothing.
            present = bool(q.strip()) and (q in hay or norm(q) in norm(hay))
            leaked = bool(art and art.get("restricted") and present)
        else:
            leaked = bool(ev.get("restricted") and q.strip())
        if leaked and human:
            out.append(violation(ev.get("step"), "§8.3", f"restricted artefact {ev.get('artifact_id')} quoted verbatim"))
        elif leaked:
            ctx.setdefault("restricted_quoted_unrouted", []).append(ev.get("artifact_id"))
        # §8.7 — raw contact details carried to a human (syntactic pattern, not a phrase)
        if human and (EMAIL_RE.search(q) or has_phone(q)):
            out.append(violation(ev.get("step"), "§8.7", f"contact details carried in quote from {ev.get('artifact_id')}"))
    # §8.5 — spec §8.5: "a hypothesis may only be held at high confidence if it is supported by evidence from
    # at least two distinct sources". Either the hypothesis or the scoring block may claim high. Sources are
    # the distinct verified, current, non-bot artefact sources when the corpus is loaded (check_evidence
    # already drops stale and near-verbatim entries); attached non-bot sources otherwise.
    # Every hypothesis, not just the first: §8.5 reads "a hypothesis", unqualified. Reading hypotheses[0]
    # alone was invisible here because all 629 dossiers carry exactly one, but a second hypothesis claiming
    # unsupported high confidence would have escaped — I5 charges the count, never the confidence.
    if ctx.get("verified_sources") is not None and cx.loaded:
        sources = {s for s in ctx["verified_sources"] if s}
    else:
        sources = {ev.get("source") for ev in d.get("evidence") or [] if ev.get("source") and ev.get("source") != "bot_alert"}
    thin = len(sources) < HIGH_CONF_MIN_SOURCES
    claims_high = [h for h in d.get("hypotheses") or [] if h.get("confidence") == "high"]
    if thin and (d.get("scoring") or {}).get("confidence") == "high" and not claims_high:
        # the scoring block claims high while no hypothesis does — reported once, at the scoring step
        out.append(violation(first_hypothesis(d).get("step", step), "§8.5",
                             f"high confidence on {len(sources)} distinct verified source(s): {sorted(sources)}"))
    for h in claims_high if thin else []:
        out.append(violation(h.get("step", step), "§8.5",
                             f"high confidence on {len(sources)} distinct verified source(s): {sorted(sources)}"))
    ctx.update(visible=visible, routed=dec.get("disposition") in ("routed", "acknowledged"))
    return out
