"""
ABOUTME: Small parsing and math helpers shared by the context loader and the checks.
ABOUTME: Regexes here are syntactic (percent, dollars, email, phone) — never phrase matching.
"""

import re
from datetime import date, datetime, timezone

PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# a phone number has ≥9 digits in total; "120 - 400" style numeric ranges do not
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
WS_RE = re.compile(r"\s+")


def ts(s, warnings=None):
    """ISO-8601 with any offset (or trailing Z) → aware datetime, or None for missing / malformed. A naive
    timestamp is read as UTC and, when a `warnings` list is passed, reported there; the result is never naive."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        if warnings is not None:
            warnings.append(f"naive timestamp {s!r} read as UTC")
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


# Email / chat formatting conventions that introduce quoted or forwarded material. Syntactic, like
# the email-address regex above: they identify *structure* (a reply header, a '>' quote prefix), never
# the meaning of the words. The operative message is everything before the first such marker.
QUOTE_MARKERS = [
    re.compile(r"(?m)^On .{3,160}? wrote:\s*$"),                       # "On 20 Sep 2025, Kenji Iyer wrote:"
    re.compile(r"(?mi)^-{2,}\s*(original message|forwarded message)\s*-{2,}\s*$"),
    re.compile(r"(?mi)^(from|de|von):\s.+\n(sent|to|date|an|para):\s", re.M),   # pasted header block
    re.compile(r"(?m)^>"),                                              # '>'-prefixed quoted line
]


def split_quoted(text):
    """(head, tail, marker) — head is the new message, tail the quoted/forwarded material, or ('', text, None)
    when nothing is quoted. If the whole text is quoted (no head), head is ''."""
    text = text or ""
    first, which = None, None
    for rx in QUOTE_MARKERS:
        m = rx.search(text)
        if m and (first is None or m.start() < first):
            first, which = m.start(), rx.pattern[:24]
    if first is None:
        return text, "", None
    return text[:first].rstrip(), text[first:], which


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
    """Did a human get this signal? Notified, routed, or a human pre-empted it (spec §4.4: a person
    got there first). Independent of how the signal ended — routed→expired still reached one."""
    if d.get("notifications"):
        return True
    for e in d.get("lifecycle") or []:
        if (e.get("from_state"), e.get("to_state")) == ("scored", "routed") or e.get("trigger") == "human_preempt":
            return True
    return False


def days_to_renewal(d, account_facts):
    """Prefer renewal_date − opened_at; fall back to the agent's metadata figure. None if unknown."""
    rd = day(account_facts.get("renewal_date")) if account_facts else None
    od = day(d.get("opened_at"))
    if rd and od:
        return (rd - od).days
    v = (d.get("metadata") or {}).get("days_to_renewal")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
