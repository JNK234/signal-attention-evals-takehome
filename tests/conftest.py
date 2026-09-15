"""
ABOUTME: Shared test fixtures — a spec-conformant synthetic account/owner/artefact/dossier and a small
ABOUTME: telemetry builder — so every test module can break one thing and assert one rule.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from signal_eval import SignalEvaluator  # noqa: E402
from signal_eval.labellers import TableLabeller  # noqa: E402

ACCOUNT = {"account_id": "acct_T", "name": "Test Co", "tier": "mid_market", "region": "emea", "industry": "x",
           "arr_annual": 100_000, "seats_contracted": 100, "materiality_floor": 18_000, "flags": [],
           "collector": "legacy", "owner_id": "u_T", "champion": "Ada Lovelace", "economic_buyer": "Alan Turing"}
OWNER = {"owner_id": "u_T", "name": "Owner", "role": "csm", "timezone": "Europe/Berlin", "locale": "de-DE", "preferred_channel": "slack"}
ARTIFACT = {"artifact_id": "art_T1", "account_id": "acct_T", "type": "support_ticket", "source": "support_ticket",
            "restricted": False, "timestamp": "2026-03-01T10:00:00Z", "author": "Ada", "author_type": "customer",
            "lang": "en", "subject": "Question", "text": "We are planning a backfill of about 90M rows."}
OTHER_ARTIFACT = dict(ARTIFACT, artifact_id="art_OTHER", account_id="acct_Z")


def happy_dossier():
    """A spec-conformant dossier: full happy path, material, in-window notification."""
    return {
        "signal_id": "sig_T", "account_id": "acct_T", "detector": "seat_decay", "detector_version": "v3.0",
        "opened_at": "2026-03-02T08:00:00Z", "closed_at": "2026-03-03T12:00:00Z",
        "lifecycle": [
            {"step": 0, "from_state": "idle", "to_state": "candidate", "at": "2026-03-02T08:00:00Z", "trigger": "detector:seat_decay", "reason": ""},
            {"step": 1, "from_state": "candidate", "to_state": "corroborating", "at": "2026-03-02T09:00:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 2, "from_state": "corroborating", "to_state": "hypothesis_formed", "at": "2026-03-02T10:00:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 3, "from_state": "hypothesis_formed", "to_state": "evidence_pending", "at": "2026-03-02T11:00:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 4, "from_state": "evidence_pending", "to_state": "evidence_received", "at": "2026-03-02T12:00:00Z", "trigger": "enrichment_returned", "reason": ""},
            {"step": 5, "from_state": "evidence_received", "to_state": "scored", "at": "2026-03-02T13:00:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 6, "from_state": "scored", "to_state": "routed", "at": "2026-03-02T14:00:00Z", "trigger": "agent_action", "reason": ""},
            {"step": 7, "from_state": "routed", "to_state": "acknowledged", "at": "2026-03-03T12:00:00Z", "trigger": "owner_acknowledged", "reason": ""},
        ],
        "hypotheses": [{"step": 2, "hypothesis": "budget_pressure", "confidence": "medium", "evidence_refs": ["art_T1"], "rationale": ""}],
        "evidence": [{"step": 2, "artifact_id": "art_T1", "source": "support_ticket", "restricted": False,
                      "attached_at": "2026-03-02T09:30:00Z", "quote": "We are planning a backfill of about 90M rows."}],
        "metrics_claimed": [],
        "actions": [
            {"step": 2, "action": "attach_evidence", "at": "2026-03-02T09:30:00Z", "params": {"artifact_id": "art_T1"}},
            {"step": 3, "action": "request_enrichment", "at": "2026-03-02T11:00:00Z", "params": {"hypothesis": "budget_pressure", "window_days": 7, "metrics": ["dau_seats"]}},
            {"step": 5, "action": "score_signal", "at": "2026-03-02T13:00:00Z", "params": {"severity": "P2", "arr_at_risk": 30_000, "confidence": "medium"}},
            {"step": 6, "action": "notify_owner", "at": "2026-03-02T14:00:00Z", "params": {"channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1, "severity": "P2"}},
        ],
        "notifications": [{"step": 6, "at": "2026-03-02T14:00:00Z", "channel": "slack", "locale": "de-DE", "owner_id": "u_T", "attempt": 1}],
        "scoring": {"severity": "P2", "confidence": "medium", "arr_at_risk": 30_000, "scored_at": "2026-03-02T13:00:00Z"},
        "decision": {"disposition": "acknowledged", "recommended_play": "renewal_risk_review", "customer_visible": False, "reason": None},
        "metadata": {"account_tier": "mid_market", "region": "emea", "arr_annual": 100_000, "materiality_floor": 18_000,
                     "seats_contracted": 100, "owner_id": "u_T", "owner_timezone": "Europe/Berlin", "owner_locale": "de-DE",
                     "owner_preferred_channel": "slack", "account_flags": [], "collector": "legacy", "days_to_renewal": 120},
    }


def telemetry(values_before, values_after, end=date(2026, 3, 1), status="ok", account_id="acct_T"):
    """14 daily rows for `account_id` ending at `end`: 7 before, 7 after."""
    rows = []
    for i, v in enumerate(list(values_before) + list(values_after)):
        d = end - timedelta(days=13 - i)
        rows.append({"account_id": account_id, "date": d.isoformat(), "dau_seats": v, "api_calls": 1000, "query_p95_ms": 500,
                     "error_rate_pct": 0.1, "dashboards_created": 1, "data_volume_gb": 1.0, "ingest_status": status,
                     "ingested_at": f"{(d + timedelta(days=1)).isoformat()}T03:00:00Z"})
    return rows


def drop_days(rows, *days):
    """Rows without the given dates — a missing account-day, never a zero-filled one."""
    gone = {d.isoformat() for d in days}
    return [r for r in rows if r["date"] not in gone]


def set_row(rows, d, **fields):
    """Overwrite fields (metric values, ingest_status, ...) on the row dated `d`; returns rows."""
    for r in rows:
        if r["date"] == d.isoformat():
            r.update(fields)
    return rows


def _loaded(e):
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT, OTHER_ARTIFACT], [happy_dossier()])
    return e


@pytest.fixture
def ev():
    """Evaluator loaded with the synthetic corpus and an empty TableLabeller: every block is readable and
    every score is 0, so every verdict is False (no model in unit tests)."""
    return _loaded(SignalEvaluator(labeller=TableLabeller({})))


@pytest.fixture
def ev_nolabeller():
    """Same corpus, no labeller at all: every label read is unverifiable."""
    return _loaded(SignalEvaluator(labeller=None))


def with_labels(ev, table):
    """Swap in a TableLabeller {substring: {label: score}} — a fresh score cache and trigger index come with it,
    so earlier reads cannot leak into the new meaning."""
    ev.cx.set_labeller(TableLabeller(table))
    ev.cx.account_triggers.clear()
    return ev


def rules(result):
    return {v["rule"] for v in result["violations"]}


def explain(ev, d):
    """evaluate() plus "_facts" — what tests read when they assert on extracted facts, not just violations."""
    return ev.explain(d)


def spec_sections():
    """Every section number the specification actually defines, resolved from spec.tex.

    LaTeX does not write the numbers — they are positional, so they are counted the way the renderer
    counts them: each \\section increments the major number and resets the minor, each \\subsection
    increments the minor. Returns {"4", "4.7", "8", "8.4", ...}.

    This exists because the guards it serves used to assert only that the *shape* of a citation looked
    like a section and that spec.tex contained any section at all — a constant-true test that accepted
    §999.999. A citation that cannot be resolved here is not a citation.
    """
    import re
    from pathlib import Path
    out, major, minor = set(), 0, 0
    for line in (Path(__file__).resolve().parents[1] / "spec.tex").read_text().splitlines():
        if re.match(r"\s*\\section\{", line):
            major, minor = major + 1, 0
            out.add(str(major))
        elif re.match(r"\s*\\subsection\{", line):
            minor += 1
            out.add(f"{major}.{minor}")
    return out


def spec_section_titles():
    """{"4.8": "Transition Matrix", "8.4": "Cross-Tenant Isolation", ...} — the number AND what it is about.

    Resolving only the number let every §4.x citation sit one subsection off for months: the code called the
    transition matrix §4.7 (it is §4.8) and human pre-empt §4.4 (it is §4.5), and the guard passed because
    §4.7 and §4.4 both exist. A citation has to point at the right place, not just a real one.
    """
    import re
    from pathlib import Path
    out, major, minor = {}, 0, 0
    for line in (Path(__file__).resolve().parents[1] / "spec.tex").read_text().splitlines():
        m = re.match(r"\s*\\(sub)?section\{(.+?)\}", line)
        if not m:
            continue
        if m.group(1):
            minor += 1
            out[f"{major}.{minor}"] = m.group(2)
        else:
            major, minor = major + 1, 0
            out[str(major)] = m.group(2)
    return out
