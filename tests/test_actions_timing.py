"""
ABOUTME: Known-answer tests for I4 placed in time (spec §5 Table 7 / §7 I4): edge actions sit on a transition
ABOUTME: instant, attach happens during an attach state, notify follows scored→routed, params carry their keys.
"""

from conftest import explain, happy_dossier, rules

NOTIFY_PARAMS = {"channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1, "severity": "P2"}
SCORE_PARAMS = {"severity": "P2", "arr_at_risk": 30_000, "confidence": "medium"}


def i4(result):
    return [v for v in result["violations"] if v["rule"] == "§5"]


# ── 1. the happy path: every edge action sits on its transition instant ──────────────────────

def test_edge_actions_at_their_transition_instants_are_valid(ev):
    """request_enrichment at 11:00 = step 3, score_signal at 13:00 = step 5, notify at 14:00 = step 6."""
    assert not i4(ev.evaluate(happy_dossier()))


# ── 2. an edge action between transitions rides nothing ─────────────────────────────────────

def test_score_signal_between_transitions_is_I4(ev):
    d = happy_dossier()
    d["actions"][2]["at"] = "2026-03-02T13:30:00Z"          # scored at 13:00, routed at 14:00
    v = i4(ev.evaluate(d))
    assert len(v) == 1 and v[0]["step"] == 5
    assert "score_signal" in v[0]["explanation"] and "no transition" in v[0]["explanation"] and "scored" in v[0]["explanation"]


# ── 3/4. attach_evidence is judged by the state at its instant ──────────────────────────────

def test_attach_during_from_state_of_declared_step_is_valid(ev):
    """Corpus convention: attach at 09:30 while corroborating, declared on step 2 (corroborating→hypothesis_formed
    at 10:00). Declaring it on step 1 (candidate→corroborating, the entry that *enters* the state) is also fine."""
    d = happy_dossier()
    assert not i4(ev.evaluate(d))
    d["actions"][0]["step"] = 1
    d["evidence"][0]["step"] = 1
    assert not i4(ev.evaluate(d))


def test_attach_during_scored_is_I4(ev):
    d = happy_dossier()
    d["actions"][0].update(step=5, at="2026-03-02T13:30:00Z")   # scored at 13:00, routed at 14:00
    d["evidence"][0].update(step=5, attached_at="2026-03-02T13:30:00Z")
    v = i4(ev.evaluate(d))
    assert len(v) == 1 and v[0]["step"] == 5
    assert "attach_evidence" in v[0]["explanation"] and "during scored" in v[0]["explanation"]


# ── 5/6. notify_owner follows the scored→routed edge ─────────────────────────────────────────

def test_notify_after_routed_edge_is_valid(ev):
    d = happy_dossier()
    d["actions"][3]["at"] = "2026-03-02T14:30:00Z"            # routed at 14:00; 15:30 Berlin, inside the window
    d["notifications"][0]["at"] = "2026-03-02T14:30:00Z"
    assert not i4(ev.evaluate(d))


def test_notify_during_scored_with_score_params_is_I4_naming_both_defects(ev):
    d = happy_dossier()
    d["actions"][3] = {"step": 5, "action": "notify_owner", "at": "2026-03-02T13:30:00Z", "params": dict(SCORE_PARAMS)}
    v = i4(ev.evaluate(d))
    assert len(v) == 1 and v[0]["step"] == 5
    x = v[0]["explanation"]
    assert "notify_owner" in x and "evidence_received→scored" in x and "scored→routed" in x
    assert "channel" in x and "locale" in x and "owner_id" in x and "attempt" in x


# ── 7. an action name outside Table 7 ────────────────────────────────────────────────────────

def test_unknown_action_is_I4(ev):
    d = happy_dossier()
    d["actions"].append({"step": 5, "action": "escalate", "at": "2026-03-02T13:00:00Z", "params": {}})
    v = i4(ev.evaluate(d))
    assert len(v) == 1 and v[0]["step"] == 5 and "unknown action 'escalate'" in v[0]["explanation"]


# ── 8. a re-request carrying `note` instead of the metric window: I4 (params, no edge) and Q4 ─

def test_request_enrichment_with_note_params_after_return_is_I4_and_Q4(ev):
    d = happy_dossier()                                          # enrichment_returned at step 4, 12:00
    d["actions"].insert(3, {"step": 4, "action": "request_enrichment", "at": "2026-03-02T12:30:00Z",
                            "params": {"hypothesis": "budget_pressure", "window_days": 7, "note": "duplicate request"}})
    r = ev.evaluate(d)
    v = i4(r)
    assert len(v) == 1 and v[0]["step"] == 4
    assert "request_enrichment" in v[0]["explanation"] and "metrics" in v[0]["explanation"] and "no transition" in v[0]["explanation"]
    assert any(x["rule"] == "Q4" and "already returned" in x["explanation"] for x in r["violations"])


# ── 9. right instant, right edge, wrong declared step ───────────────────────────────────────

def test_declared_step_mismatch_is_I4(ev):
    d = happy_dossier()
    d["actions"][2]["step"] = 4                                  # score_signal at 13:00 is step 5's edge
    v = i4(ev.evaluate(d))
    assert len(v) == 1 and v[0]["step"] == 4
    assert "declared step 4" in v[0]["explanation"] and "step 5" in v[0]["explanation"]


# ── closed_at after the terminal edge is recorded, not penalised ────────────────────────────

def test_closed_at_after_terminal_is_a_fact_not_a_violation(ev):
    d = happy_dossier()
    d["lifecycle"] = d["lifecycle"][:6] + [{"step": 6, "from_state": "scored", "to_state": "suppressed",
                                           "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": "immaterial"}]
    d["actions"] = d["actions"][:3] + [{"step": 6, "action": "suppress", "at": "2026-03-02T14:00:00Z", "params": {"reason": "immaterial"}}]
    d["notifications"] = []
    d["decision"].update(disposition="suppressed", recommended_play="watch_only")
    d["closed_at"] = "2026-03-09T14:00:00Z"
    r = explain(ev, d)
    assert r["_facts"]["closed_at_after_terminal"] is True
    assert not ({"I2", "§5"} & rules(r))
    d["closed_at"] = "2026-03-02T14:00:00Z"
    assert explain(ev, d)["_facts"]["closed_at_after_terminal"] is False
