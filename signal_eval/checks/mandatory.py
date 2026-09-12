"""
ABOUTME: Mandatory-route triggers — spec §8.1. Structural rules run first; the NLI labels may add
ABOUTME: triggers the rules missed or remove only the structural cancel proxy (asymmetric). A trigger
ABOUTME: is a violation when no human was ever notified. Account-level unattached triggers are separate.
"""

from datetime import timedelta

from ..spec import BILLING_DISPUTE_PCT, DEPARTURE_WINDOW_DAYS, RECENT_EVIDENCE_DAYS, violation
from ..util import days_to_renewal, dollars, first_hypothesis, reached_human, ts

HARD_TRIGGERS = {"billing_dispute", "buyer_or_champion_departure"}
ACCOUNT_LOOKBACK_DAYS = 30    # a trigger this far before opened_at should have been found by the lookup
ACCOUNT_LOOKAHEAD_DAYS = 14   # ...or arrived while the signal was still open


def _label_of(a):
    return a.get("_label") or {}


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
        disputed = det_fired or (bool(lab) and not lab.get("unverifiable") and lab.get("billing_dispute"))
        if not disputed:
            continue
        amt = dollars(a.get("text"))
        if amt is None:
            return {"billing_dispute(amount unverified)"}
        if not arr or amt >= BILLING_DISPUTE_PCT * arr:
            return {"billing_dispute"}
    if det_fired and not bills:
        return {"billing_dispute(amount unverified)"}
    return set()


def _from_model(verified, near_renewal):
    """What the verified, current evidence says under the NLI labels."""
    trig, labelled, unverifiable = set(), 0, False
    for a in verified or []:
        lab = _label_of(a)
        if not lab:
            continue
        if lab.get("unverifiable"):
            unverifiable = True
            continue
        labelled += 1
        if lab.get("triggers_belong_elsewhere"):
            continue
        cust = a.get("author_type") in ("customer", None)   # None = cold-path quote, author unknown
        if lab.get("cancel_intent") and cust:
            trig.add("cancel_intent")
        if lab.get("legal_reference"):
            trig.add("legal_reference")
        if lab.get("security_incident") and cust:
            trig.add("security_incident")
        if lab.get("departure") and near_renewal:
            trig.add("buyer_or_champion_departure")
    return trig, labelled, unverifiable


def _unattached_account_triggers(d, cx, near_renewal):
    """Trigger-bearing artefacts on this account near the signal that are not in its evidence."""
    if not cx.account_triggers:
        return []
    opened = ts(d.get("opened_at"))
    if not opened:
        return []
    closed = ts(d.get("closed_at")) or opened + timedelta(days=ACCOUNT_LOOKAHEAD_DAYS)
    lo, hi = opened - timedelta(days=ACCOUNT_LOOKBACK_DAYS), max(closed, opened + timedelta(days=ACCOUNT_LOOKAHEAD_DAYS))
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

    structural = _structural(d, ctx, verified, near_renewal) | _billing(d, ctx, verified)
    model_trig, labelled, unverifiable = _from_model(verified, near_renewal)
    model_used = labelled > 0
    if model_used:
        trig = set(structural)
        added = model_trig - structural
        removed = set()
        if "cancel_intent" in structural and "cancel_intent" not in model_trig:
            trig.discard("cancel_intent")
            removed.add("cancel_intent")
        trig |= added
        source = {"structural": sorted(structural), "model": sorted(model_trig), "added_by_model": sorted(added),
                  "removed_by_model": sorted(removed), "proxy": False,
                  "label_source": "artifact" if cx.loaded else "quote"}
    else:
        trig = structural
        source = {"structural": sorted(structural), "model": None, "added_by_model": [], "removed_by_model": [],
                  "proxy": True, "unverifiable": unverifiable}
    human = reached_human(d)
    missed = _unattached_account_triggers(d, cx, near_renewal)
    det = d.get("detector")
    ctx.update(triggers=trig, trigger_source=source, reached_human=human, account_triggers_unattached=missed,
               security_review=det == "security_review_opened" or any(ev.get("source") == "security_review" for ev in d.get("evidence") or []))

    out = []
    disp = (d.get("decision") or {}).get("disposition")
    if trig and not human:
        step = next((e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") in ("suppressed", "expired")), 0)
        certain = model_used or bool(trig & HARD_TRIGGERS)
        out.append(violation(step, "P1", f"{disp or 'closed'} without any human notified despite mandatory-route trigger(s) {sorted(trig)}"
                             + ("" if certain else " — structural proxy, text not read"), 1.0 if certain else 0.6))
    elif missed and not human:
        step = next((e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") in ("suppressed", "expired")), 0)
        kinds = sorted({t for _, _, ts_ in missed for t in ts_})
        out.append(violation(step, "P1", f"{disp or 'closed'} without any human notified while the account carried an unattached written trigger {kinds} "
                             f"({', '.join(aid for _, aid, _ in missed[:3])}) — cross-source lookup missed it", 0.8))
    return out
