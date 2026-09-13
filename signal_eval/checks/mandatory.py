"""
ABOUTME: Mandatory-route triggers — spec §8.1. Structural rules run first; the NLI labels may add
ABOUTME: triggers the rules missed or remove only the structural cancel proxy (asymmetric). A trigger is a
ABOUTME: violation when the signal was suppressed or no human was notified. Unattached triggers are separate.
"""

from datetime import timedelta

from ..spec import BILLING_DISPUTE_PCT, DEPARTURE_WINDOW_DAYS, RECENT_EVIDENCE_DAYS, violation
from ..util import days_to_renewal, dollars, first_hypothesis, norm, reached_human, ts

HARD_TRIGGERS = {"billing_dispute", "buyer_or_champion_departure"}
ACCOUNT_LOOKBACK_DAYS = 30    # a trigger this far before opened_at should have been found by the lookup
ACCOUNT_LOOKAHEAD_DAYS = 14   # ...or arrived while the signal was still open


def _label_of(a):
    return a.get("_label") or {}


def departure_attributed(art, account):
    """spec §8.1 bullet 4: 'Departure of the economic buyer or named champion'. A departure label is the
    trigger only when it is about one of those two people: the artefact's author IS one of them, or the
    text names one of them. The label dict carries only the max over the generic and the {names}
    hypothesis sentences (classifier.assemble), so the name test stands in for the per-sentence score.
    Matching a person's name from the account record is identity matching, not phrase matching."""
    names = [n for n in ((account or {}).get("champion"), (account or {}).get("economic_buyer")) if n]
    if not names:
        return False
    author = norm(art.get("author"))
    text = norm((art.get("subject") or "") + "\n" + (art.get("text") or ""))
    return any(author == norm(n) or norm(n) in text for n in names)


def _structural(d, ctx, verified, near_renewal):
    """What the dossier's structure alone says. No text is read."""
    trig = set()
    det = d.get("detector")
    hyp = first_hypothesis(d).get("hypothesis")
    opened = ts(d.get("opened_at"))
    # cancel-intent proxy: the churn-keyword detector fired and there is current, non-chat customer text
    if det == "exec_churn_language":
        if verified is None:
            trig.add("cancel_intent(unverified)")
        else:
            cust = [a for a in verified if a.get("author_type") == "customer"]
            fresh_direct = [a for a in cust if a.get("type") != "chat_message" and opened and ts(a.get("timestamp"))
                            and abs((opened - ts(a["timestamp"])).days) <= RECENT_EVIDENCE_DAYS]
            if fresh_direct or len({a.get("type") for a in cust}) >= 2:
                trig.add("cancel_intent")
    # departure inside the renewal window, via the agent's own hypothesis
    if hyp == "champion_departure" and near_renewal:
        trig.add("buyer_or_champion_departure")
    return trig


def _billing(d, ctx, verified):
    """spec §8.1 bullet 5 — a billing dispute above 5% of ARR.
    Structural: the billing_dispute detector (spec §3.1: "a billing event is marked disputed or overdue")
    plus the amount read syntactically from the attached billing_event.
    Detector-agnostic: any attached billing_event the model reads as disputed/overdue, same amount test."""
    arr = ctx["acct"].get("arr")
    det_fired = d.get("detector") == "billing_dispute"
    if verified is None:
        return {"billing_dispute(amount unverified)"} if det_fired else set()
    bills = [a for a in verified if a.get("source") == "billing_event" or a.get("type") == "billing_event"]
    for a in bills:
        lab = _label_of(a)
        # without the detector, only a readable label that says "disputed" makes this event a dispute;
        # an unlabelled / unverifiable / not-disputed billing_event is just an invoice record
        disputed = det_fired or (bool(lab) and not lab.get("unverifiable") and bool(lab.get("billing_dispute")))
        if not disputed:
            continue
        amt = dollars(a.get("text"))
        if amt is None:
            return {"billing_dispute(amount unverified)"}
        # spec §8.1 bullet 5: "exceeding 5% of the account's annual ARR" — strictly greater; 5% exactly is not
        if not arr or amt > BILLING_DISPUTE_PCT * arr:
            return {"billing_dispute"}
    if det_fired and not bills:
        return {"billing_dispute(amount unverified)"}
    return set()


def _from_model(verified, near_renewal, account):
    """What the verified, current evidence says under the NLI labels.
    Returns (triggers, n_labelled, customer_text_fully_read): the last is True only when EVERY verified
    customer-authored artefact carries a readable label — the condition for trusting the model's silence."""
    trig, labelled, customer_read = set(), 0, True
    for a in verified or []:
        lab = _label_of(a)
        if not lab or lab.get("unverifiable"):
            if a.get("author_type") == "customer":
                customer_read = False
            continue
        labelled += 1
        if lab.get("triggers_belong_elsewhere"):
            continue
        cust = a.get("author_type") in ("customer", None)   # None = cold-path quote, author unknown
        # spec §8.1 bullet 1: "from a customer-side author"
        if lab.get("cancel_intent") and cust:
            trig.add("cancel_intent")
        # spec §8.1 bullet 2 names no author: a reference to counsel / breach / reserved rights / a regulator
        # counts whoever wrote it down
        if lab.get("legal_reference"):
            trig.add("legal_reference")
        # spec §8.1 bullet 3: "raised by the customer"
        if lab.get("security_incident") and cust:
            trig.add("security_incident")
        # spec §8.1 bullet 4: the economic buyer or named champion, within 90 days of renewal
        if lab.get("departure") and near_renewal and departure_attributed(a, account):
            trig.add("buyer_or_champion_departure")
    return trig, labelled, customer_read


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
    verified = ctx.get("verified")
    dtr = days_to_renewal(d, ctx["acct"])
    near_renewal = dtr is not None and 0 <= dtr <= DEPARTURE_WINDOW_DAYS
    ctx["days_to_renewal"] = dtr

    account = cx.accounts.get(d.get("account_id")) if cx.loaded else None   # champion / economic_buyer live here
    structural = _structural(d, ctx, verified, near_renewal) | _billing(d, ctx, verified)
    model_trig, labelled, customer_read = _from_model(verified, near_renewal, account)
    model_used = labelled > 0
    proxy_kept = False
    if model_used:
        trig = set(structural)
        added = model_trig - structural
        removed = set()
        if "cancel_intent" in structural and "cancel_intent" not in model_trig:
            # the model's silence outweighs the structural proxy only if it actually read every customer
            # artefact; one unlabelled customer text leaves the proxy standing (asymmetric, recall first)
            if customer_read:
                trig.discard("cancel_intent")
                removed.add("cancel_intent")
            else:
                proxy_kept = True
        trig |= added
        source = {"structural": sorted(structural), "model": sorted(model_trig), "added_by_model": sorted(added),
                  "removed_by_model": sorted(removed), "proxy": proxy_kept, "customer_text_read": customer_read,
                  "label_source": "artifact" if cx.loaded else "quote"}
    else:
        trig = structural
        source = {"structural": sorted(structural), "model": None, "added_by_model": [], "removed_by_model": [],
                  "proxy": True, "unverifiable": not customer_read}
    human = reached_human(d)
    missed = _unattached_account_triggers(d, cx, near_renewal)
    det = d.get("detector")
    ctx.update(triggers=trig, trigger_source=source, reached_human=human, account_triggers_unattached=missed,
               security_review=det == "security_review_opened" or any(ev.get("source") == "security_review" for ev in d.get("evidence") or []))

    out = []
    disp = (d.get("decision") or {}).get("disposition")
    lifecycle = d.get("lifecycle") or []
    suppressed = disp == "suppressed" or any(e.get("to_state") == "suppressed" for e in lifecycle)
    # spec §8.1: the agent "must put a human on the signal, and must not suppress it" — two duties, either
    # breach is P1. spec §4.3: suppression is forbidden when a trigger is present, so notifying first is no cure.
    if trig and (suppressed or not human):
        step = next((e.get("step") for e in lifecycle if e.get("to_state") in ("suppressed", "expired")), 0)
        # certain when a hard structural trigger or a model-read trigger is present, or the model read the text
        # and left only structural triggers standing; a proxy the model could not rule out stays a proxy
        certain = bool(trig & HARD_TRIGGERS) or bool(trig & model_trig) or (model_used and not proxy_kept)
        what = ("suppressed after a human was notified" if suppressed and human
                else f"{disp or 'closed'} without any human notified")
        out.append(violation(step, "P1", f"{what} despite mandatory-route trigger(s) {sorted(trig)}"
                             + ("" if certain else " — structural proxy, text not read"), certain=certain))
    elif missed and not human:
        step = next((e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") in ("suppressed", "expired")), 0)
        kinds = sorted({t for _, _, ts_ in missed for t in ts_})
        out.append(violation(step, "P1", f"{disp or 'closed'} without any human notified while the account carried an unattached written trigger {kinds} "
                             f"({', '.join(aid for _, aid, _ in missed[:3])}) — cross-source lookup missed it", certain=False))
    return out
