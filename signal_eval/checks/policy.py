"""
ABOUTME: Policy checks that need only the dossier — spec §8.2 contact restrictions,
ABOUTME: §8.3 restricted-source containment, §8.5 confidence honesty, §8.7 contact details.
"""

from ..spec import CUSTOMER_VISIBLE_PLAYS, HIGH_CONF_MIN_SOURCES, RESTRICT_FLAGS, violation
from ..util import EMAIL_RE, first_hypothesis, has_phone, reached_human


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
            leaked = bool(art and art.get("restricted") and q.strip() and q in hay)
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
    confs = {first_hypothesis(d).get("confidence"), (d.get("scoring") or {}).get("confidence")}
    if "high" in confs:
        if ctx.get("verified_sources") is not None and cx.loaded:
            sources = {s for s in ctx["verified_sources"] if s}
        else:
            sources = {ev.get("source") for ev in d.get("evidence") or [] if ev.get("source") and ev.get("source") != "bot_alert"}
        if len(sources) < HIGH_CONF_MIN_SOURCES:
            out.append(violation(first_hypothesis(d).get("step", step), "§8.5",
                                 f"high confidence on {len(sources)} distinct verified source(s): {sorted(sources)}"))
    ctx.update(visible=visible, routed=dec.get("disposition") in ("routed", "acknowledged"))
    return out
