"""
ABOUTME: Timing checks — spec §6 (owner-local window, re-notify spacing, time-to-attention,
ABOUTME: staleness) and §8.6 notification fit (channel / locale).
"""

from zoneinfo import ZoneInfo

from ..spec import MAX_NOTIFICATIONS, NOTIFY_WINDOW, RENOTIFY_MIN_HOURS, STALENESS_DAYS, TTA_HOURS, violation
from ..util import ts


def check_timing(d, ctx, cx):
    out = []
    sev = (d.get("scoring") or {}).get("severity")
    own = cx.owner_facts(d)
    notes = [n for n in d.get("notifications") or [] if ts(n.get("at"))]
    notes.sort(key=lambda n: n["at"])
    opened = ts(d.get("opened_at"))
    try:
        tz = ZoneInfo(own["tz"]) if own["tz"] else None
    except Exception as exc:
        tz = None
        # spec §6.1 cannot be checked without the owner's clock — say so, never skip silently
        ctx.setdefault("errors", []).append({"check": "check_timing", "error": f"unknown owner timezone {own['tz']!r}: {exc!r}"})
        if notes and sev != "P0":
            out.append(violation(notes[0].get("step"), "§6.1", f"owner timezone {own['tz']!r} unknown; notification window unverifiable "
                                 f"({len(notes)} notification(s), severity {sev})", certain=False))
    expected_owner = (d.get("metadata") or {}).get("owner_id")
    for n in notes:
        at = ts(n["at"])
        # T1 — window is the owner's local clock, not the account's and not UTC; P0 may page any hour
        if tz and sev != "P0":
            local = at.astimezone(tz)
            if not (NOTIFY_WINDOW[0] <= local.hour < NOTIFY_WINDOW[1]):
                out.append(violation(n.get("step"), "§6.1", f"notified at {local.strftime('%H:%M')} {own['tz']} (window 08:00–19:00, severity {sev})"))
        # §8.6 — declared channel and locale, and the right person
        if own["channel"] and n.get("channel") != own["channel"]:
            out.append(violation(n.get("step"), "§8.6", f"channel {n.get('channel')} ≠ owner preferred {own['channel']}"))
        if own["locale"] and n.get("locale") != own["locale"]:
            out.append(violation(n.get("step"), "§8.6", f"locale {n.get('locale')} ≠ owner locale {own['locale']}"))
        if expected_owner and n.get("owner_id") and n.get("owner_id") != expected_owner:
            out.append(violation(n.get("step"), "§8.6", f"notified {n.get('owner_id')} but the account's owner is {expected_owner}"))
    # T2 — spacing and count, counted only while the owner has not acknowledged (spec §6.2)
    ack_at = next((ts(e.get("at")) for e in d.get("lifecycle") or [] if e.get("to_state") == "acknowledged" and ts(e.get("at"))), None)
    pre_ack = [n for n in notes if not ack_at or ts(n["at"]) <= ack_at]
    if len(pre_ack) > MAX_NOTIFICATIONS:
        out.append(violation(pre_ack[-1].get("step"), "§6.2", f"{len(pre_ack)} notifications before acknowledgement (max {MAX_NOTIFICATIONS})"))
    for a, b in zip(pre_ack, pre_ack[1:]):
        gap = (ts(b["at"]) - ts(a["at"])).total_seconds() / 3600
        if gap < RENOTIFY_MIN_HOURS:
            out.append(violation(b.get("step"), "§6.2", f"re-notified after {gap:.1f}h (min {RENOTIFY_MIN_HOURS}h)"))
    # T3 — opened_at to first notify_owner, only when the signal actually went scored→routed
    life = d.get("lifecycle") or []
    routed_edge = next((e for e in life if (e.get("from_state"), e.get("to_state")) == ("scored", "routed")), None)
    if routed_edge and opened:
        if notes:
            hours = (ts(notes[0]["at"]) - opened).total_seconds() / 3600
            # An unrecognised severity still has a deadline: silently skipping T3 would read as a pass.
            # checks/mandatory.py falls back to the P1 target for the same case; the two must not disagree.
            target = TTA_HOURS.get(sev, TTA_HOURS["P1"])
            if sev not in TTA_HOURS:
                ctx.setdefault("errors", []).append(
                    {"check": "check_timing", "error": f"unknown severity {sev!r}; T3 judged against the P1 target"})
            if target and hours > target:
                # the breach is certain; how late (hours vs target) is stated, not folded into the severity
                out.append(violation(notes[0].get("step"), "§6.3", f"first notification {hours:.1f}h after open; {sev} target {target}h"))
        else:
            out.append(violation(routed_edge.get("step"), "§6.3", "routed with no notify_owner"))
    # T4 — staleness. Premature: expired with <14 idle days. Overdue: still in a progression state
    # with >14 idle days after the last evidence and no acknowledgement, judged at the last event we can see.
    # spec §6.4 counts days since evidence was attached *before* the moment being judged: an attachment
    # after expiry (itself an I2 breach) or after the last observed event must not shorten the idle time.
    ev_times = [ts(ev.get("attached_at")) for ev in d.get("evidence") or [] if ts(ev.get("attached_at"))]

    def last_evidence_before(when):
        return max([t for t in ev_times if t <= when] + ([opened] if opened else []), default=None)

    exp = next((e for e in life if e.get("to_state") == "expired"), None)
    exp_at = ts(exp.get("at")) if exp else None
    if exp and exp_at:
        last_ev = last_evidence_before(exp_at)
        idle = (exp_at - last_ev).days if last_ev else None
        if idle is not None and exp.get("from_state") != "acknowledged" and idle < STALENESS_DAYS:
            out.append(violation(exp.get("step"), "§6.4", f"expired after {idle} idle days (staleness is {STALENESS_DAYS})"))
    elif life and life[-1].get("to_state") not in ("acknowledged", "suppressed", "expired"):
        observed = [ts(e.get("at")) for e in life if ts(e.get("at"))] + [ts(n["at"]) for n in notes]
        last_seen = max(observed, default=None)
        last_ev = last_evidence_before(last_seen) if last_seen else None
        idle = (last_seen - last_ev).days if last_ev else None
        if idle is not None and idle >= STALENESS_DAYS:
            out.append(violation(life[-1].get("step"), "§6.4", f"still open {idle} days after the last evidence with no acknowledgement; should have expired", certain=False))
    return out
