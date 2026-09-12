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
    # P2 — legal_hold / mna_quiet_period: internal routing only
    hit = af["flags"] & RESTRICT_FLAGS
    if hit and visible:
        out.append(violation(step, "P2", f"customer-visible play {play} on account flagged {sorted(hit)}"))
    for ev in d.get("evidence") or []:
        q = ev.get("quote") or ""
        # P3 — restricted material quoted; the artefact record is authoritative when we have it
        art = cx.artifacts.get(ev.get("artifact_id")) if cx.loaded else None
        restricted = art.get("restricted") if art is not None else ev.get("restricted")
        if restricted and q.strip():
            out.append(violation(ev.get("step"), "P3", f"restricted artefact {ev.get('artifact_id')} quoted verbatim"
                                 + ("" if human else " (no human was notified)"), 1.0 if human else 0.3))
        # P7 — raw contact details carried to a human (syntactic pattern, not a phrase)
        if human and (EMAIL_RE.search(q) or has_phone(q)):
            out.append(violation(ev.get("step"), "P7", f"contact details carried in quote from {ev.get('artifact_id')}"))
    # P5 — high confidence needs two distinct sources that actually support it; bot alerts never count
    conf = (d.get("scoring") or {}).get("confidence")
    if conf == "high":
        if ctx.get("verified_sources") is not None and cx.loaded:
            sources = ctx["verified_sources"]
        else:
            sources = {ev.get("source") for ev in d.get("evidence") or [] if ev.get("source") != "bot_alert"}
        if len(sources) < HIGH_CONF_MIN_SOURCES:
            out.append(violation(first_hypothesis(d).get("step", step), "P5",
                                 f"high confidence on {len(sources)} distinct verified source(s): {sorted(s for s in sources if s)}"))
    ctx.update(visible=visible, routed=dec.get("disposition") in ("routed", "acknowledged"))
    return out
