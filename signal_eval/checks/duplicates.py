"""
ABOUTME: Duplicate-signal check — spec §10 Q5. Same account, same detector, opened within a week of
ABOUTME: an earlier signal that was still open. Flagged on the later duplicate; severity grows with count.
"""

from datetime import timedelta

from ..spec import DUPLICATE_WINDOW_DAYS, violation
from ..util import ts


def check_duplicates(d, ctx, cx):
    mine = ts(d.get("opened_at"))
    if not mine:
        return []
    dups = []
    for (t, det, sid) in cx.by_account.get(d.get("account_id"), []):
        if sid == d.get("signal_id") or det != d.get("detector") or not t or t > mine or (mine - t) > timedelta(days=DUPLICATE_WINDOW_DAYS):
            continue
        # spec §10 Q5 "two open signals": the earlier one is open iff closed_at is None or closed_at > this
        # signal's opened_at (half-open interval) — closing exactly when the later one opens is not overlap.
        earlier_closed = cx.closed_at.get(sid) if hasattr(cx, "closed_at") else None
        if earlier_closed is not None and earlier_closed <= mine:
            continue                      # the earlier signal was already closed — not "two open signals"
        dups.append(sid)
    ctx["duplicates"] = sorted(dups)
    if dups:
        return [violation(0, "Q5", f"duplicate of {sorted(dups)}: same account, same detector {d.get('detector')} within {DUPLICATE_WINDOW_DAYS} days while still open",
                          min(5, len(dups)))]
    return []
