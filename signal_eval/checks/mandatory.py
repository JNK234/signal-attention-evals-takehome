"""
ABOUTME: Mandatory-route triggers — spec §8.1. triggers_from_reading() reads one artefact's block Reading into
ABOUTME: confirmed / uncertain / historical / unattributed triggers; P1 fires on confirmed ∪ uncertain when suppressed or no human.
"""

from datetime import timedelta

from ..labels import TRIGGER_LABELS
from ..spec import BILLING_DISPUTE_PCT, DEPARTURE_WINDOW_DAYS, EXIT_STATES, TTA_HOURS, violation
from ..text import blocks
from ..util import author_class, days_to_renewal, money, norm, reached_human, ts

ACCOUNT_LOOKBACK_DAYS = 30    # a trigger this far before opened_at should have been found by the lookup
ACCOUNT_LOOKAHEAD_DAYS = 14   # ...or arrived while the signal was still open
ROLE_MIN_TOKENS = 2           # "Director" matches any director; "Head of Analytics" names the champion's role
BILLING_TYPES = {"billing_event"}
UNVERIFIED_BILLING = "billing_dispute(amount unverified)"


def _label_of(a):
    return a.get("_label") or {}


def _depth0_texts(reading, artifact):
    """The current, non-signature block texts. Reads text.Block objects (context.read_artifact) or the JSON-safe
    dicts (context.reading_facts); with no reading at all (no labeller) the blocks are cut from the artefact."""
    bs = (reading or {}).get("blocks")
    if bs is None:
        bs = blocks(artifact.get("subject"), artifact.get("text"))
    out = []
    for b in bs:
        depth, sig, text = ((b.depth, b.is_signature, b.text) if hasattr(b, "depth")
                            else (b.get("depth"), b.get("is_signature"), b.get("text")))
        if depth == 0 and not sig and (text or "").strip():
            out.append(text.strip())
    return out


def departure_subject(artifact, account, texts):
    """spec §8.1 bullet 4 names two people. Who the departure is about, or None when it cannot be tied to either:
    the author IS the champion / economic buyer (first-person notices), a current block names one in full, or it
    names their role (a ≥2-token title from the account record). Identity matching against the account record,
    never phrase matching; the titles are the account's own words for the role."""
    account = account or {}
    names = [(k, account.get(k)) for k in ("champion", "economic_buyer") if account.get(k)]
    author = norm(artifact.get("author"))
    for k, n in names:
        if author and author == norm(n):
            return f"{k} (author)"
    body = [norm(t) for t in texts]
    for k, n in names:
        if any(norm(n) in t for t in body):
            return f"{k} (named)"
    for k in ("champion_title", "economic_buyer_title"):
        role = norm(account.get(k))
        if len(role.split()) >= ROLE_MIN_TOKENS and any(role in t for t in body):
            return f"{k[:-len('_title')]} (role: {account.get(k)})"
    return None


def _billing(out, texts, verdict, arr, detector):
    """spec §8.1 bullet 5 — 'a billing dispute exceeding 5% of the account's annual ARR'. A1/A7: the billing_event
    is a system record read structurally — disputed when the billing_dispute detector fired (spec §3.1: it fires
    when a billing event is marked disputed or overdue) or the block reads as disputed; the amount comes from the
    same block via util.money(). ARR is in the account's currency (USD); another currency or no amount leaves
    the 5% test unverifiable. 'Exceeding' is strict: exactly 5% is not a trigger."""
    facts = out["facts"]
    if not (detector == "billing_dispute" or verdict.get("billing_dispute") is True):
        return
    amt = next((m for t in texts if (m := money(t))), None)
    if amt is None:
        out["uncertain"].add(UNVERIFIED_BILLING)
        facts["billing"] = "disputed; no amount readable"
        return
    amount, cur = amt
    if cur != "USD":
        out["uncertain"].add(UNVERIFIED_BILLING)
        facts["billing"] = f"disputed; {amount:,.0f} {cur} is not comparable to ARR in USD"
    elif not arr:
        out["uncertain"].add(UNVERIFIED_BILLING)
        facts["billing"] = f"disputed ${amount:,.0f}; ARR unknown"
    elif amount > BILLING_DISPUTE_PCT * arr:
        out["confirmed"].add("billing_dispute")
        facts["billing"] = f"disputed ${amount:,.0f} = {amount / arr:.1%} of ARR"
    else:
        facts["billing"] = f"disputed ${amount:,.0f} = {amount / arr:.1%} of ARR, not above {BILLING_DISPUTE_PCT:.0%}"


def triggers_from_reading(reading, artifact, account, dtr, detector=None, arr=None, check_window=True):
    """What one artefact says under spec §8.1, from its Reading (context.read_artifact or the JSON-safe
    lab["reading"]). Only depth-0, non-signature blocks count; a label True only in quoted history is
    `historical`; a block naming another account (reading["other_account"]) is masked. Bots contribute nothing
    but the structural billing read (A1). Attribution: cancel needs a customer author (an internal author's
    report is a fact, A9); legal needs any non-bot author (A4); security needs the customer; departure needs a
    resolved subject (departure_subject) and 0 ≤ dtr ≤ 90 (A5) — resolved outside the window is a fact,
    unresolved is `unattributed`. A verdict of None with a score (the model abstained) on a current block is
    `uncertain`, never silent; a verdict of None with no score (the block was never read) is recorded in
    facts["unread"] and left to the evaluator's single UNEVALUATED entry — an unread block is not evidence of
    anything. A quote whose author is unknown (cold path, A8) can only ever be uncertain.
    `detector` is the dossier's (billing), `arr` overrides account["arr_annual"] (cold path: metadata),
    `check_window=False` skips the renewal window (the account index applies it per signal).
    Returns {"confirmed", "uncertain", "historical", "unattributed": set, "facts": dict}."""
    out = {"confirmed": set(), "uncertain": set(), "historical": set(), "unattributed": set(), "facts": {}}
    facts = out["facts"]
    author_type = author_class(artifact.get("author_type"))   # unrecognised reads as unknown, not as not-a-customer
    texts = _depth0_texts(reading, artifact)
    verdict = (reading or {}).get("verdict") or {}
    scored = (reading or {}).get("score") or {}                 # labels the model actually produced a number for
    if artifact.get("type") in BILLING_TYPES or artifact.get("source") in BILLING_TYPES:
        _billing(out, texts, verdict, arr if arr is not None else (account or {}).get("arr_annual"), detector)
    if author_type == "bot" or reading is None:          # nothing read: nothing to say about the text
        return out
    if reading.get("other_account") is not None:
        facts["masked_other_account"] = reading["other_account"]
        return out
    out["historical"] = {label for label in reading.get("historical", ()) if label in TRIGGER_LABELS}
    if not texts:                                        # only quoted material: no current block to read
        return out
    customer, unknown = author_type == "customer", author_type is None

    def place(label, needs_customer, internal_fact):
        v = verdict.get(label)
        if v is False:
            return
        if v is None and label not in scored:            # never read: UNEVALUATED's business, not a trigger
            facts.setdefault("unread", []).append(label)
            return
        if unknown:                                      # A8: a bare quote with no author is lower-confidence
            out["uncertain"].add(label)
            facts.setdefault("author_unknown", []).append(label)
        elif needs_customer and not customer:
            if v is True:
                facts[label] = internal_fact
        elif v is True:
            out["confirmed"].add(label)
        else:                                            # the model abstained on a block that could carry it
            out["uncertain"].add(label)

    place("cancel_intent", True, "reported_by_internal")         # spec §8.1 bullet 1 "from a customer-side author"
    place("legal_reference", False, None)                        # bullet 2 names no author
    place("security_incident", True, "reported_by_internal")     # bullet 3 "raised by the customer"

    v = verdict.get("departure")                                 # bullet 4: the economic buyer or named champion
    if v is None and "departure" not in scored:
        facts.setdefault("unread", []).append("departure")
    elif v is not False:
        name = "buyer_or_champion_departure"
        subject = departure_subject(artifact, account, texts)
        if subject is None:
            if v is True:
                out["unattributed"].add("departure")
                facts["departure"] = "unattributed"
        elif not check_window or (dtr is not None and 0 <= dtr <= DEPARTURE_WINDOW_DAYS):
            (out["confirmed"] if v is True and not unknown else out["uncertain"]).add(name)
            facts[name] = subject
        elif dtr is None:
            out["uncertain"].add(name)
            facts[name] = f"{subject}; renewal date unknown"
        else:
            facts[name] = f"{subject}; outside the {DEPARTURE_WINDOW_DAYS}-day renewal window (days_to_renewal={dtr})"
    return out


def _unattached_account_triggers(d, cx, near_renewal):
    """Trigger-bearing artefacts on this account near the signal that are not in its evidence."""
    if not cx.account_triggers:
        return []
    opened = ts(d.get("opened_at"))
    if not opened:
        return []
    # spec §8.1 asks that a trigger "present in the evidence" reach a human; text written after the agent
    # closed the signal was never available to it, so the window ends at close (or after 14 days if still open)
    closed = ts(d.get("closed_at"))
    lookahead = opened + timedelta(days=ACCOUNT_LOOKAHEAD_DAYS)
    lo, hi = opened - timedelta(days=ACCOUNT_LOOKBACK_DAYS), min(closed, lookahead) if closed else lookahead
    attached = {ev.get("artifact_id") for ev in d.get("evidence") or []}
    out = []
    for when, aid, trig in cx.account_triggers.get(d.get("account_id"), []):
        if aid in attached or not (lo <= when <= hi):
            continue
        kinds = {t for t in trig if t != "buyer_or_champion_departure" or near_renewal}
        if kinds:
            out.append((when.isoformat(), aid, sorted(kinds)))
    return sorted(out)


def check_mandatory_route(d, ctx, cx):
    dtr = days_to_renewal(d, ctx["acct"])
    near_renewal = dtr is not None and 0 <= dtr <= DEPARTURE_WINDOW_DAYS
    ctx["days_to_renewal"] = dtr

    account = cx.accounts.get(d.get("account_id")) if cx.loaded else None   # champion / economic_buyer live here
    det = d.get("detector")
    merged = {k: set() for k in ("confirmed", "uncertain", "historical", "unattributed")}
    per_artifact = {}
    for a in ctx.get("verified") or []:
        t = triggers_from_reading(_label_of(a).get("reading"), a, account, dtr, detector=det, arr=ctx["acct"].get("arr"))
        for k in merged:
            merged[k] |= t[k]
        if t["facts"] or any(t[k] for k in merged):
            per_artifact[a.get("artifact_id")] = {**{k: sorted(t[k]) for k in merged}, "facts": t["facts"]}
    if det == "billing_dispute" and not any(a.get("type") in BILLING_TYPES or a.get("source") in BILLING_TYPES
                                            for a in ctx.get("verified") or []):
        merged["uncertain"].add(UNVERIFIED_BILLING)   # the dispute is a system event; its amount is not in evidence
    trig = merged["confirmed"] | merged["uncertain"]
    source = {**{k: sorted(v) for k, v in merged.items()}, "per_artifact": per_artifact}
    human = reached_human(d)
    missed = _unattached_account_triggers(d, cx, near_renewal)
    ctx.update(triggers=trig, trigger_source=source, reached_human=human, account_triggers_unattached=missed,
               security_review=det == "security_review_opened" or any(ev.get("source") == "security_review" for ev in d.get("evidence") or []))

    out = []
    disp = (d.get("decision") or {}).get("disposition")
    lifecycle = d.get("lifecycle") or []
    suppressed = disp == "suppressed" or any(e.get("to_state") == "suppressed" for e in lifecycle)
    # spec §8.1: the agent "must put a human on the signal, and must not suppress it" — two duties, either
    # breach is P1. spec §4.3: suppression is forbidden when a trigger is present, so notifying first is no cure.
    # spec §8.1 gives the duty, §6.3 gives the clock: a signal still in a progression state, not closed, inside its
    # time-to-attention target has failed neither yet — recorded as a pending trigger, not a missed route. Past the
    # target it is a late route (uncertain: the record may simply end before the notification).
    in_flight = not suppressed and not human and not d.get("closed_at") and ctx.get("final_state") not in EXIT_STATES
    if trig and in_flight:
        opened, seen = ts(d.get("opened_at")), [ts(e.get("at")) for e in lifecycle if ts(e.get("at"))]
        hours = (max(seen) - opened).total_seconds() / 3600 if opened and seen else None
        target = TTA_HOURS.get((d.get("scoring") or {}).get("severity"), TTA_HOURS["P1"])
        if hours is not None and hours <= target:
            ctx["pending_trigger"] = {"triggers": sorted(trig), "hours_open": round(hours, 1), "target_hours": target}
            return out
        out.append(violation(lifecycle[-1].get("step") if lifecycle else 0, "P1",
                             f"still open {hours if hours is None else round(hours, 1)}h after opening with no human notified, past the "
                             f"{target}h target, despite mandatory-route trigger(s): confirmed {sorted(merged['confirmed'])}", certain=False))
        return out
    if trig and (suppressed or not human):
        step = next((e.get("step") for e in lifecycle if e.get("to_state") in ("suppressed", "expired")), 0)
        what = ("suppressed after a human was notified" if suppressed and human
                else f"{disp or 'closed'} without any human notified")
        detail = f"confirmed {sorted(merged['confirmed'])}" + (f", uncertain {sorted(merged['uncertain'])}" if merged["uncertain"] else "")
        out.append(violation(step, "P1", f"{what} despite mandatory-route trigger(s): {detail}", certain=bool(merged["confirmed"])))
    elif missed and not human:
        step = next((e.get("step") for e in lifecycle if e.get("to_state") in ("suppressed", "expired")), 0)
        kinds = sorted({t for _, _, ts_ in missed for t in ts_})
        out.append(violation(step, "P1", f"{disp or 'closed'} without any human notified while the account carried an unattached written trigger {kinds} "
                             f"({', '.join(aid for _, aid, _ in missed[:3])}) — cross-source lookup missed it", certain=False))
    return out
