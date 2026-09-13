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
    except Exception:
        tz = None
    expected_owner = (d.get("metadata") or {}).get("owner_id")
    for n in notes:
        at = ts(n["at"])
        # T1 — window is the owner's local clock, not the account's and not UTC; P0 may page any hour
        if tz and sev != "P0":
            local = at.astimezone(tz)
            if not (NOTIFY_WINDOW[0] <= local.hour < NOTIFY_WINDOW[1]):
                out.append(violation(n.get("step"), "T1", f"notified at {local.strftime('%H:%M')} {own['tz']} (window 08:00–19:00, severity {sev})"))
        # P6 — declared channel and locale, and the right person
        if own["channel"] and n.get("channel") != own["channel"]:
            out.append(violation(n.get("step"), "P6", f"channel {n.get('channel')} ≠ owner preferred {own['channel']}"))
        if own["locale"] and n.get("locale") != own["locale"]:
            out.append(violation(n.get("step"), "P6", f"locale {n.get('locale')} ≠ owner locale {own['locale']}"))
        if expected_owner and n.get("owner_id") and n.get("owner_id") != expected_owner:
            out.append(violation(n.get("step"), "P6", f"notified {n.get('owner_id')} but the account's owner is {expected_owner}"))
    # T2 — spacing and count, counted only while the owner has not acknowledged (spec §6.2)
    ack_at = next((ts(e.get("at")) for e in d.get("lifecycle") or [] if e.get("to_state") == "acknowledged" and ts(e.get("at"))), None)
    pre_ack = [n for n in notes if not ack_at or ts(n["at"]) <= ack_at]
    if len(pre_ack) > MAX_NOTIFICATIONS:
        out.append(violation(pre_ack[-1].get("step"), "T2", f"{len(pre_ack)} notifications before acknowledgement (max {MAX_NOTIFICATIONS})"))
    for a, b in zip(pre_ack, pre_ack[1:]):
        gap = (ts(b["at"]) - ts(a["at"])).total_seconds() / 3600
        if gap < RENOTIFY_MIN_HOURS:
            out.append(violation(b.get("step"), "T2", f"re-notified after {gap:.1f}h (min {RENOTIFY_MIN_HOURS}h)"))
    # T3 — opened_at to first notify_owner, only when the signal actually went scored→routed
    life = d.get("lifecycle") or []
    routed_edge = next((e for e in life if (e.get("from_state"), e.get("to_state")) == ("scored", "routed")), None)
    if routed_edge and opened:
        if notes:
            hours = (ts(notes[0]["at"]) - opened).total_seconds() / 3600
            target = TTA_HOURS.get(sev)
            if target and hours > target:
                out.append(violation(notes[0].get("step"), "T3", f"first notification {hours:.1f}h after open; {sev} target {target}h",
                                     min(1.0, 0.5 + 0.5 * (hours - target) / target)))
        else:
            out.append(violation(routed_edge.get("step"), "T3", "routed with no notify_owner"))
    # T4 — staleness. Premature: expired with <14 idle days. Overdue: still in a progression state
    # with >14 idle days after the last evidence and no acknowledgement, judged at the last event we can see.
    ev_times = [ts(ev.get("attached_at")) for ev in d.get("evidence") or [] if ts(ev.get("attached_at"))]
    last_ev = max(ev_times + ([opened] if opened else []), default=None)
    exp = next((e for e in life if e.get("to_state") == "expired"), None)
    if exp and last_ev and ts(exp.get("at")):
        idle = (ts(exp["at"]) - last_ev).days
        if exp.get("from_state") != "acknowledged" and idle < STALENESS_DAYS:
            out.append(violation(exp.get("step"), "T4", f"expired after {idle} idle days (staleness is {STALENESS_DAYS})"))
    elif life and last_ev and life[-1].get("to_state") not in ("acknowledged", "suppressed", "expired"):
        last_seen = max([ts(e.get("at")) for e in life if ts(e.get("at"))] + [ts(n["at"]) for n in notes] + [last_ev])
        idle = (last_seen - last_ev).days
        if idle >= STALENESS_DAYS:
            out.append(violation(life[-1].get("step"), "T4", f"still open {idle} days after the last evidence with no acknowledgement; should have expired", 0.7))
    return out
