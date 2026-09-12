"""
ABOUTME: State-machine checks — spec §4 (transition matrix), §5 (actions), §7 I1–I5.
ABOUTME: A finite-state acceptor over lifecycle[]; every edge is tested against the allowed sets.
"""

from collections import Counter

from ..spec import (ACTION_EDGE, ATTACH_STATES, BACKWARD_LOW_ONLY, EXIT_STATES, FORWARD_EDGES, RANK,
                    SUPPRESS_FORBIDDEN_FROM, TIMEOUT_EDGE, violation)
from ..util import ts


def check_transitions(d, ctx, cx):
    """I1 backward, I2 after exit, I3 continuity, TM edge/trigger. Also records final state and visits."""
    out = []
    hyps = sorted(d.get("hypotheses") or [], key=lambda h: h.get("step") or 0)
    cur, closed, exit_at = None, False, None
    visits, backward = Counter(), 0
    for e in d.get("lifecycle") or []:
        s, f, t, trig = e.get("step"), e.get("from_state"), e.get("to_state"), e.get("trigger") or ""
        visits[t] += 1
        if f == t:                       # spec §4.2: staying in the current state is always valid
            cur = t
            continue
        if closed:
            out.append(violation(s, "I2", f"transition {f}→{t} after exit state at step {s}"))
            continue
        if cur is not None and f != cur:
            out.append(violation(s, "I3", f"lifecycle discontinuity: expected from_state {cur}, got {f}"))
        edge = (f, t)
        conf = next((h.get("confidence") for h in reversed(hyps) if (h.get("step") or 0) <= (s or 0)), None)
        if edge in FORWARD_EDGES:
            pass
        elif edge in BACKWARD_LOW_ONLY:
            backward += 1
            if conf != "low":
                out.append(violation(s, "I1", f"backward {f}→{t} while hypothesis held at {conf} confidence"))
        elif edge == TIMEOUT_EDGE:
            if trig != "enrichment_timeout":
                out.append(violation(s, "TM", f"{f}→{t} without enrichment_timeout (trigger={trig})"))
        elif t == "acknowledged":
            if trig != "human_preempt":
                out.append(violation(s, "TM", f"{f}→acknowledged without human_preempt (trigger={trig})"))
        elif t == "suppressed":
            if f in SUPPRESS_FORBIDDEN_FROM:
                out.append(violation(s, "TM", f"{f}→suppressed is not an allowed edge"))
            if not (e.get("reason") or "").strip():
                out.append(violation(s, "TM", "suppression without a stated reason", 0.5))
        elif t == "expired":
            pass
        elif f in RANK and t in RANK and RANK[t] < RANK[f]:
            backward += 1
            out.append(violation(s, "I1", f"backward transition {f}→{t}"))
        else:
            out.append(violation(s, "TM", f"{f}→{t} is not in the transition matrix"))
        if t in EXIT_STATES:
            closed, exit_at = True, ts(e.get("at"))
        cur = t
    ctx.update(exit_at=exit_at, final_state=cur, visits=visits, backward=backward)
    return out


def check_actions(d, ctx, cx):
    """I4: every action must ride its allowed edge; nothing after an exit state (I2)."""
    out = []
    life = {e.get("step"): e for e in d.get("lifecycle") or []}
    supp_edges = {e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") == "suppressed"}
    supp_actions = set()
    for a in d.get("actions") or []:
        s, name = a.get("step"), a.get("action")
        e = life.get(s)
        at = ts(a.get("at"))
        if ctx.get("exit_at") and at and at > ctx["exit_at"]:
            out.append(violation(s, "I2", f"action {name} at {a['at']} after exit state"))
            continue
        if e is None:
            out.append(violation(s, "I4", f"action {name} references step {s} with no lifecycle entry"))
            continue
        edge = (e["from_state"], e["to_state"])
        if name == "attach_evidence":
            if e["from_state"] not in ATTACH_STATES:
                out.append(violation(s, "I4", f"attach_evidence during {e['from_state']} (allowed: corroborating, evidence_received)"))
        elif name == "suppress":
            supp_actions.add(s)
            if e["to_state"] != "suppressed":
                out.append(violation(s, "I4", f"suppress action on {edge[0]}→{edge[1]}, not a transition to suppressed"))
        elif name in ACTION_EDGE:
            if edge not in ACTION_EDGE[name]:
                out.append(violation(s, "I4", f"{name} on {edge[0]}→{edge[1]}; allowed only on {sorted(ACTION_EDGE[name])}"))
            if name == "score_signal" and edge == TIMEOUT_EDGE and e.get("trigger") != "enrichment_timeout":
                out.append(violation(s, "I4", "score_signal on evidence_pending→scored without enrichment_timeout"))
    for s in supp_edges - supp_actions:
        out.append(violation(s, "I4", "transition to suppressed without a suppress action", 0.5))
    return out


def check_hypothesis_count(d, ctx, cx):
    """I5 (count part): exactly one hypothesis. The content part lives in quality.py."""
    hs = d.get("hypotheses") or []
    if len(hs) != 1:
        return [violation(hs[0].get("step") if hs else 0, "I5", f"{len(hs)} hypotheses; spec requires exactly one")]
    return []
