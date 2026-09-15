"""
ABOUTME: State-machine checks — spec §4 (transition matrix), §5 (actions), §7 I1–I5.
ABOUTME: A finite-state acceptor over lifecycle[]; every edge is tested against the allowed sets.
"""

from collections import Counter

from ..spec import (ACTION_EDGE, ACTION_PARAMS, ATTACH_STATES, BACKWARD_LOW_ONLY, CONFIDENCE_LEVELS, DETECTORS,
                    EXIT_STATES, FORWARD_EDGES, HYPOTHESIS_CLASSES, RANK, SUPPRESS_FORBIDDEN_FROM, TIMEOUT_EDGE,
                    violation)
from ..util import reached_human, ts

# spec §4.1 Table (happy path): two forward edges are driven by a platform event, not an agent action —
# evidence_pending→evidence_received "on enrichment_returned", routed→acknowledged "on owner_acknowledged".
# Every other forward edge is "agent action" and carries no trigger requirement.
FORWARD_EDGE_TRIGGER = {
    ("evidence_pending", "evidence_received"): "enrichment_returned",
    ("routed", "acknowledged"): "owner_acknowledged",
}
# spec §4.5: a human_preempt event moves a signal to acknowledged from any progression state, routed included.
PREEMPT_TRIGGER = "human_preempt"

# spec §3.2 Table: "Every signal is assigned exactly one of these seven classes."



def check_transitions(d, ctx, cx):
    """I1 backward, I2 after exit, I3 continuity, TM edge/trigger. Also records final state and visits."""
    out = []
    hyps = sorted(d.get("hypotheses") or [], key=lambda h: h.get("step") or 0)
    cur, closed, exit_at, prev_at = None, False, None, None
    visits, backward, timed_out = Counter(), 0, False
    back_at = []          # when each backward move happened — Q1's "without adding evidence" needs the times
    detector = d.get("detector")
    for e in d.get("lifecycle") or []:
        s, f, t, trig = e.get("step"), e.get("from_state"), e.get("to_state"), e.get("trigger") or ""
        at = ts(e.get("at"))
        # I3 — timestamps strictly increasing (data dictionary: "transition timestamps are strictly increasing")
        if at and prev_at and at <= prev_at:
            out.append(violation(s, "I3", f"transition at {e.get('at')} is not after the previous one", certain=False))
        prev_at = at or prev_at
        if f != t:
            visits[t] += 1        # Q1 counts arrivals in a state; staying put (§4.2) is not a visit
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
                out.append(violation(s, "§4.8", f"opened by trigger {trig!r}, expected detector:{detector}", certain=False))
        elif edge in FORWARD_EDGES:
            # spec §4.1 Table: the two system-event edges must carry their event (preempt also lands on acknowledged, §4.5)
            need = FORWARD_EDGE_TRIGGER.get(edge)
            if need and trig != need and not (t == "acknowledged" and trig == PREEMPT_TRIGGER):
                out.append(violation(s, "§4.8", f"{f}→{t} on trigger {trig!r}, spec §4.1 requires {need}", certain=False))
        elif edge in BACKWARD_LOW_ONLY:
            backward += 1
            if at:
                back_at.append(at)
            if conf != "low":
                out.append(violation(s, "I1", f"backward {f}→{t} while hypothesis held at {conf} confidence"))
        elif edge == TIMEOUT_EDGE:
            if trig != "enrichment_timeout":
                out.append(violation(s, "§4.8", f"{f}→{t} without enrichment_timeout (trigger={trig})"))
            else:
                timed_out = True
        elif t == "acknowledged":
            if trig != "human_preempt":
                out.append(violation(s, "§4.8", f"{f}→acknowledged without human_preempt (trigger={trig})"))
        elif t == "suppressed":
            if f in SUPPRESS_FORBIDDEN_FROM:
                out.append(violation(s, "§4.8", f"{f}→suppressed is not an allowed edge"))
            if not (e.get("reason") or "").strip():
                out.append(violation(s, "§4.8", "suppression without a stated reason", certain=False))
            if timed_out:
                # spec §4.5: a signal that timed out waiting for data must still reach a human
                out.append(violation(s, "§4.8", "suppressed after enrichment_timeout; §4.5 requires the signal to be routed"))
        elif t == "expired":
            # spec §3.3 / §6.4: expiry is the staleness_timeout system event
            if trig != "staleness_timeout":
                out.append(violation(s, "§4.8", f"{f}→expired without staleness_timeout (trigger={trig})", certain=False))
            if timed_out and not reached_human(d):
                # spec §4.5: "a signal that timed out waiting for data must still reach a human" — letting it
                # expire un-routed and un-notified is the same failure as suppressing it
                out.append(violation(s, "§4.8", "expired after enrichment_timeout without reaching a human; §4.5 requires the signal to be routed"))
        elif f in RANK and t in RANK and RANK[t] < RANK[f]:
            backward += 1
            if at:
                back_at.append(at)
            out.append(violation(s, "I1", f"backward transition {f}→{t}"))
        else:
            out.append(violation(s, "§4.8", f"{f}→{t} is not in the transition matrix"))
        if t in EXIT_STATES:
            closed, exit_at = True, ts(e.get("at"))
        cur = t
    ctx.update(exit_at=exit_at, final_state=cur, visits=visits, backward=backward, backward_at=back_at,
               enrichment_timed_out=timed_out)
    return out


def state_timeline(lifecycle):
    """Transitions in time order as [(at, step, from_state, to_state)]; entries without a parseable `at` are
    left out because they cannot place an action in time."""
    tl = []
    for e in lifecycle or []:
        at = ts(e.get("at"))
        if at:
            tl.append((at, e.get("step"), e.get("from_state"), e.get("to_state")))
    tl.sort(key=lambda x: x[0])
    return tl


def state_at(timeline, t):
    """The state the machine is in at instant t. A transition at exactly t has already been applied
    (data dictionary: strictly increasing timestamps make the state at any instant unambiguous).
    None before the first transition."""
    state = None
    for at, _, _, to in timeline:
        if at <= t:
            state = to
        else:
            break
    return state


EDGE_WORDS = {name: " or ".join(f"{f}→{t}" for f, t in sorted(edges)) for name, edges in ACTION_EDGE.items()}
EDGE_WORDS["suppress"] = "a transition to suppressed"


def check_actions(d, ctx, cx):
    """I4 — every action placed in *time* against the lifecycle (spec §5 Table 7, §7 I4). Convention:
    - edge actions (request_enrichment, score_signal, suppress, enrichment_timeout) happen *at* a transition: a
      lifecycle entry with the same `at`, an edge Table 7 allows for that action, and the action's declared step;
    - notify_owner rides scored→routed but may trail it: the latest transition at or before the action must be
      that edge, and the declared step must be its step;
    - attach_evidence happens *during* a state: the state at the action's instant must be corroborating or
      evidence_received, and the declared step must be the entry that leaves that state (from_state — the corpus
      convention: attach, then advance) or enters it (to_state);
    - `params` must carry the keys ACTION_PARAMS lists for the action (extra keys tolerated);
    - an action name outside Table 7 is I4.
    One I4 per action, listing every defect. Anything after an exit state is I2 first (spec §7 I2), and a
    closed_at later than the exit edge is recorded as a fact, not a violation."""
    out = []
    exit_at = ctx.get("exit_at")
    closed = ts(d.get("closed_at"))
    ctx["closed_at_after_terminal"] = bool(exit_at and closed and closed > exit_at)
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
    life = d.get("lifecycle") or []
    by_step = {e.get("step"): e for e in life}
    timeline = state_timeline(life)
    supp_edges = {e.get("step") for e in life if e.get("to_state") == "suppressed"}
    supp_actions = set()
    for a in d.get("actions") or []:
        s, name = a.get("step"), a.get("action")
        at = ts(a.get("at"))
        if exit_at and at and at > exit_at:
            out.append(violation(s, "I2", f"action {name} at {a['at']} after exit state"))
            continue
        if name not in ACTION_PARAMS:
            out.append(violation(s, "§5", f"unknown action {name!r}; spec §5 Table 7 lists {sorted(ACTION_PARAMS)}"))
            continue
        defects = []
        missing = ACTION_PARAMS[name] - set(a.get("params") or {})
        if missing:
            defects.append(f"params missing {sorted(missing)}")
        if at is None:
            defects.append("no parseable timestamp, so it cannot be placed against the lifecycle")
        elif name == "attach_evidence":
            st = state_at(timeline, at)
            if st not in ATTACH_STATES:
                defects.append(f"attached at {a['at']} during {st or 'no state (before the first transition)'} "
                               f"(allowed: {', '.join(sorted(ATTACH_STATES))})")
            e = by_step.get(s)
            if e is None:
                defects.append(f"declared step {s} has no lifecycle entry")
            elif st in ATTACH_STATES and st not in (e.get("from_state"), e.get("to_state")):
                defects.append(f"declared step {s} is {e.get('from_state')}→{e.get('to_state')}, not the stay in {st}")
        elif name == "notify_owner":
            prior = [x for x in timeline if x[0] <= at]
            if not prior:
                defects.append(f"at {a['at']} the signal had not opened; notify_owner is allowed only after scored→routed")
            else:
                _, last_step, f, t = prior[-1]
                if (f, t) != ("scored", "routed"):
                    defects.append(f"at {a['at']} the last transition was {f}→{t} (step {last_step}); "
                                   f"notify_owner is allowed only after scored→routed")
                elif last_step != s:
                    defects.append(f"declared step {s} but the scored→routed edge it follows is step {last_step}")
        else:
            here = [x for x in timeline if x[0] == at]
            if not here:
                defects.append(f"at {a['at']} no transition happens (state {state_at(timeline, at)}); "
                               f"{name} must ride {EDGE_WORDS[name]}")
            else:
                ok = [x for x in here if (x[3] == "suppressed" if name == "suppress" else (x[2], x[3]) in ACTION_EDGE[name])]
                if not ok:
                    _, _, f, t = here[0]
                    defects.append(f"rides {f}→{t} at {a['at']}; allowed only on {EDGE_WORDS[name]}")
                else:
                    x = next((x for x in ok if x[1] == s), ok[0])
                    if x[1] != s:
                        defects.append(f"declared step {s} but the transition at {a['at']} is step {x[1]}")
                    if name == "score_signal" and (x[2], x[3]) == TIMEOUT_EDGE and (by_step.get(x[1]) or {}).get("trigger") != "enrichment_timeout":
                        defects.append("score_signal on evidence_pending→scored without enrichment_timeout")
                    if name == "suppress":
                        supp_actions.add(x[1])
        if defects:
            out.append(violation(s, "§5", f"{name}: " + "; ".join(defects)))
    for s in supp_edges - supp_actions:
        out.append(violation(s, "§5", "transition to suppressed without a suppress action", certain=False))
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
        # spec §3.2 "Each hypothesis also carries a confidence level: high, medium or low." An unrecognised
        # value is not a weaker "high": §8.5 reads `"high" in confs` and I1 reads `conf != "low"`, so an
        # unknown value silently escapes the two-source test and reads as a backward-edge violation.
        if h.get("confidence") not in CONFIDENCE_LEVELS:
            out.append(violation(h.get("step"), "I5",
                                 f"confidence {h.get('confidence')!r} is not one of {sorted(CONFIDENCE_LEVELS)} (spec §3.2)"))
    # spec §3.1 "A signal opens when exactly one of eight detectors fires" — a name outside the eight means
    # the record does not say which detector opened the signal.
    det = d.get("detector")
    if det is not None and det not in DETECTORS:
        out.append(violation(0, "I5", f"unknown detector {det!r}; spec §3.1 lists {sorted(DETECTORS)}"))
    return out
