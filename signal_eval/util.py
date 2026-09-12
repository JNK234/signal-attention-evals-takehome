"""
ABOUTME: Small parsing and math helpers shared by the context loader and the checks.
ABOUTME: Regexes here are syntactic (percent, dollars, email, phone) — never phrase matching.
"""

import re
from datetime import date, datetime

PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# a phone number has ≥9 digits in total; "120 - 400" style numeric ranges do not
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
WS_RE = re.compile(r"\s+")


def ts(s):
    """ISO-8601 with trailing Z → aware datetime, or None for missing / malformed."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def day(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def num(x):
    """Coerce a numeric field that may arrive as int, float, numeric string, or None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def pct(claim):
    """Signed percentage out of a claim string like 'api_calls -61% week over week'."""
    m = PCT_RE.search(claim or "")
    return float(m.group(1)) if m else None


def dollars(text):
    m = DOLLAR_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def has_phone(text):
    return any(sum(ch.isdigit() for ch in m.group(0)) >= 9 for m in PHONE_RE.finditer(text or ""))


def norm(s):
    """Whitespace / smart-punctuation normalisation for the I6 near-match."""
    return WS_RE.sub(" ", (s or "").replace("’", "'").replace("“", '"')
                     .replace("”", '"').replace("–", "-").replace("—", "-")).strip().lower()


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def first_hypothesis(d):
    hs = d.get("hypotheses") or []
    return hs[0] if hs else {}


def reached_human(d):
    """Did a human get notified? Independent of how the signal ended (routed→expired still reached one)."""
    if d.get("notifications"):
        return True
    return any((e.get("from_state"), e.get("to_state")) == ("scored", "routed") for e in d.get("lifecycle") or [])


def days_to_renewal(d, account_facts):
    """Prefer renewal_date − opened_at; fall back to the agent's metadata figure. None if unknown."""
    rd = day(account_facts.get("renewal_date")) if account_facts else None
    od = day(d.get("opened_at"))
    if rd and od:
        return (rd - od).days
    v = (d.get("metadata") or {}).get("days_to_renewal")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
