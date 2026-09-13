"""
ABOUTME: Materiality checks — spec §9 M1–M5. Integer comparisons between arr_at_risk, the
ABOUTME: account's floor and ARR, plus consistency of every later arr_at_risk restatement.
"""

from ..spec import violation
from ..util import dollars, num


def check_materiality(d, ctx, cx):
    out = []
    af = ctx["acct"]
    sc = d.get("scoring") or {}
    risk, arr, floor = num(sc.get("arr_at_risk")), num(af["arr"]), num(af["floor"])
    step = next((a.get("step") for a in d.get("actions") or [] if a.get("action") == "score_signal"), 0)
    # spec §9 M3 "when the agent routes a signal" / M4 "must not route it as-is": routing is the act of
    # taking the scored→routed edge, not the final disposition — routed then expired was still routed.
    routed = any((e.get("from_state"), e.get("to_state")) == ("scored", "routed") for e in d.get("lifecycle") or [])
    if risk is not None and arr is not None and risk > arr:
        out.append(violation(step, "M1", f"arr_at_risk {risk:,.0f} > arr_annual {arr:,.0f}"))
    if floor is not None and arr is not None and floor > arr:
        out.append(violation(step, "M2", f"materiality_floor {floor:,.0f} > arr_annual {arr:,.0f}"))
    # M3/M4 — routed must be material. Below-floor is M4 (its own rule); above-ARR is already M1, so M3
    # only reports the routed case once, tagged as the routing violation it is.
    if routed and None not in (risk, floor, arr) and risk < floor:
        out.append(violation(step, "M4", f"routed with arr_at_risk {risk:,.0f} below floor {floor:,.0f}"))
    elif routed and None not in (risk, arr) and risk > arr:
        out.append(violation(step, "M3", f"routed with arr_at_risk {risk:,.0f} above arr_annual {arr:,.0f}", 0.5))
    # M5 — restatements: 12× is the MRR/ARR bug; equal to arr_annual is the other named confusion
    for m in d.get("metrics_claimed") or []:
        if m.get("metric") != "arr_at_risk":
            continue
        stated = dollars(m.get("claim"))
        if stated is None or not risk or abs(stated - risk) < 1:
            continue
        ratio = max(stated, risk) / max(min(stated, risk), 1)
        if abs(ratio - 12) < 0.6:
            why = "12× restatement (MRR/ARR confusion)"
        elif arr and abs(stated - arr) < 1:
            why = "arr_annual restated as arr_at_risk"
        else:
            why = "inconsistent restatement with no enrichment revision"
        out.append(violation(m.get("step"), "M5", f"arr_at_risk {risk:,.0f} later stated as ${stated:,.0f}: {why}"))
    for a in d.get("actions") or []:
        if a.get("action") == "score_signal":
            p = num((a.get("params") or {}).get("arr_at_risk"))
            if p is not None and risk is not None and p != risk:
                out.append(violation(a.get("step"), "M5", f"score_signal arr_at_risk {p:,.0f} ≠ scoring.arr_at_risk {risk:,.0f}"))
    if af["mismatch"]:
        ctx["context_loss"] = af["mismatch"]
    return out
