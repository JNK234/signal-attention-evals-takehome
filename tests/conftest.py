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
            {"step": 3, "action": "request_enrichment", "at": "2026-03-02T11:00:00Z", "params": {}},
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


def telemetry(values_before, values_after, end=date(2026, 3, 1), status="ok"):
    """14 daily rows for acct_T ending at `end`: 7 before, 7 after."""
    rows = []
    for i, v in enumerate(list(values_before) + list(values_after)):
        d = end - timedelta(days=13 - i)
        rows.append({"account_id": "acct_T", "date": d.isoformat(), "dau_seats": v, "api_calls": 1000, "query_p95_ms": 500,
                     "error_rate_pct": 0.1, "dashboards_created": 1, "data_volume_gb": 1.0, "ingest_status": status,
                     "ingested_at": f"{(d + timedelta(days=1)).isoformat()}T03:00:00Z"})
    return rows


@pytest.fixture
def ev():
    """Evaluator loaded with the synthetic corpus, classifier off (no model in unit tests)."""
    e = SignalEvaluator(use_classifier=False)
    e.load_context([ACCOUNT], [OWNER], telemetry([50] * 7, [50] * 7), [ARTIFACT, OTHER_ARTIFACT], [happy_dossier()])
    return e


def rules(result):
    return {v["rule"] for v in result["violations"]}
