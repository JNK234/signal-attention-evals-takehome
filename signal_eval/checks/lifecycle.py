"""
ABOUTME: State-machine checks — spec §4 (transition matrix), §5 (actions), §7 I1–I5.
ABOUTME: A finite-state acceptor over lifecycle[]; every edge is tested against the allowed sets.
"""

from collections import Counter

from ..spec import (ACTION_EDGE, ATTACH_STATES, BACKWARD_LOW_ONLY, EXIT_STATES, FORWARD_EDGES, RANK,
                    SUPPRESS_FORBIDDEN_FROM, TIMEOUT_EDGE, violation)
from ..util import reached_human, ts

# spec §4.1 Table (happy path): two forward edges are driven by a platform event, not an agent action —
# evidence_pending→evidence_received "on enrichment_returned", routed→acknowledged "on owner_acknowledged".
# Every other forward edge is "agent action" and carries no trigger requirement.
FORWARD_EDGE_TRIGGER = {
    ("evidence_pending", "evidence_received"): "enrichment_returned",
    ("routed", "acknowledged"): "owner_acknowledged",
}
# spec §4.4: a human_preempt event moves a signal to acknowledged from any progression state, routed included.
PREEMPT_TRIGGER = "human_preempt"

# spec §3.2 Table: "Every signal is assigned exactly one of these seven classes."
HYPOTHESIS_CLASSES = frozenset({"no_hypothesis", "champion_departure", "budget_pressure", "product_gap",
                                "onboarding_failure", "reliability_erosion", "benign_variation"})


def check_transitions(d, ctx, cx):
    """I1 backward, I2 after exit, I3 continuity, TM edge/trigger. Also records final state and visits."""
    out = []
    hyps = sorted(d.get("hypotheses") or [], key=lambda h: h.get("step") or 0)
    cur, closed, exit_at, prev_at = None, False, None, None
    visits, backward, timed_out = Counter(), 0, False
    detector = d.get("detector")
    for e in d.get("lifecycle") or []:
        s, f, t, trig = e.get("step"), e.get("from_state"), e.get("to_state"), e.get("trigger") or ""
        at = ts(e.get("at"))
        visits[t] += 1
        # I3 — timestamps strictly increasing (data dictionary: "transition timestamps are strictly increasing")
        if at and prev_at and at <= prev_at:
            out.append(violation(s, "I3", f"transition at {e.get('at')} is not after the previous one", certain=False))
        prev_at = at or prev_at
        if f == t:
            # spec §4.2: staying in the *current* state is always valid. §7 I3: a signal is in exactly one
            # state, so "staying" in a state the machine is not in is a discontinuity. The machine does not
            # move on an invalid stay, so the next real edge is judged against the state it was actually in.
            if cur is not None and f != cur:
                out.append(violation(s, "I3", f"lifecycle discontinuity: self-transition {f}→{t} while in state {cur}"))
            else:
                cur = t
            continue
        if closed:
            out.append(violation(s, "I2", f"transition {f}→{t} after exit state at step {s}"))
            continue
        if cur is not None and f != cur:
            out.append(violation(s, "I3", f"lifecycle discontinuity: expected from_state {cur}, got {f}"))
        edge = (f, t)
        conf = next((h.get("confidence") for h in reversed(hyps) if (h.get("step") or 0) <= (s or 0)), None)
        if edge == ("idle", "candidate"):
            # spec §4.1: a signal opens when a detector fires
            if detector and trig != f"detector:{detector}":
                out.append(violation(s, "TM", f"opened by trigger {trig!r}, expected detector:{detector}", certain=False))
        elif edge in FORWARD_EDGES:
            # spec §4.1 Table: the two system-event edges must carry their event (preempt also lands on acknowledged, §4.4)
            need = FORWARD_EDGE_TRIGGER.get(edge)
            if need and trig != need and not (t == "acknowledged" and trig == PREEMPT_TRIGGER):
                out.append(violation(s, "TM", f"{f}→{t} on trigger {trig!r}, spec §4.1 requires {need}", certain=False))
        elif edge in BACKWARD_LOW_ONLY:
            backward += 1
            if conf != "low":
                out.append(violation(s, "I1", f"backward {f}→{t} while hypothesis held at {conf} confidence"))
        elif edge == TIMEOUT_EDGE:
            if trig != "enrichment_timeout":
                out.append(violation(s, "TM", f"{f}→{t} without enrichment_timeout (trigger={trig})"))
            else:
                timed_out = True
        elif t == "acknowledged":
            if trig != "human_preempt":
                out.append(violation(s, "TM", f"{f}→acknowledged without human_preempt (trigger={trig})"))
        elif t == "suppressed":
            if f in SUPPRESS_FORBIDDEN_FROM:
                out.append(violation(s, "TM", f"{f}→suppressed is not an allowed edge"))
            if not (e.get("reason") or "").strip():
                out.append(violation(s, "TM", "suppression without a stated reason", certain=False))
            if timed_out:
                # spec §4.5: a signal that timed out waiting for data must still reach a human
                out.append(violation(s, "TM", "suppressed after enrichment_timeout; §4.5 requires the signal to be routed"))
        elif t == "expired":
            # spec §3.3 / §6.4: expiry is the staleness_timeout system event
            if trig != "staleness_timeout":
                out.append(violation(s, "TM", f"{f}→expired without staleness_timeout (trigger={trig})", certain=False))
            if timed_out and not reached_human(d):
                # spec §4.5: "a signal that timed out waiting for data must still reach a human" — letting it
                # expire un-routed and un-notified is the same failure as suppressing it
                out.append(violation(s, "TM", "expired after enrichment_timeout without reaching a human; §4.5 requires the signal to be routed"))
        elif f in RANK and t in RANK and RANK[t] < RANK[f]:
            backward += 1
            out.append(violation(s, "I1", f"backward transition {f}→{t}"))
        else:
            out.append(violation(s, "TM", f"{f}→{t} is not in the transition matrix"))
        if t in EXIT_STATES:
            closed, exit_at = True, ts(e.get("at"))
        cur = t
    ctx.update(exit_at=exit_at, final_state=cur, visits=visits, backward=backward, enrichment_timed_out=timed_out)
    return out


def check_actions(d, ctx, cx):
    """I4: every action must ride its allowed edge; nothing after an exit state (I2)."""
    out = []
    exit_at = ctx.get("exit_at")
    # spec §7 I2: after suppressed / expired "no further actions, notifications or evidence attachments"
    if exit_at:
        for ev_ in d.get("evidence") or []:
            at = ts(ev_.get("attached_at"))
            if at and at > exit_at:
                out.append(violation(ev_.get("step"), "I2", f"evidence {ev_.get('artifact_id')} attached at {ev_['attached_at']} after exit state"))
        for n in d.get("notifications") or []:
            at = ts(n.get("at"))
            if at and at > exit_at:
                out.append(violation(n.get("step"), "I2", f"notification attempt {n.get('attempt')} at {n['at']} after exit state"))
    life = {e.get("step"): e for e in d.get("lifecycle") or []}
    supp_edges = {e.get("step") for e in d.get("lifecycle") or [] if e.get("to_state") == "suppressed"}
    supp_actions = set()
    for a in d.get("actions") or []:
        s, name = a.get("step"), a.get("action")
        e = life.get(s)
        at = ts(a.get("at"))
        if exit_at and at and at > exit_at:
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
        out.append(violation(s, "I4", "transition to suppressed without a suppress action", certain=False))
    return out


def check_hypothesis_count(d, ctx, cx):
    """I5 (count + class part): exactly one hypothesis, drawn from the seven classes. Content lives in quality.py."""
    out = []
    hs = d.get("hypotheses") or []
    if len(hs) != 1:
        out.append(violation(hs[0].get("step") if hs else 0, "I5", f"{len(hs)} hypotheses; spec requires exactly one"))
    # spec §7 I5: "exactly one of the seven hypothesis classes in Section 3" — a name outside §3.2 is not a hypothesis
    for h in hs:
        if h.get("hypothesis") not in HYPOTHESIS_CLASSES:
            out.append(violation(h.get("step"), "I5", f"unknown hypothesis class {h.get('hypothesis')}; spec §3.2 lists {sorted(HYPOTHESIS_CLASSES)}"))
    return out
