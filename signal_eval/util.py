"""
ABOUTME: Small parsing and math helpers shared by the context loader and the checks.
ABOUTME: Regexes here are syntactic (percent, dollars, email, phone) — never phrase matching.
"""

import re
import unicodedata
from datetime import date, datetime, timezone

# Every unicode dash a writer, an autocorrect or a locale may put where a programmer types "-".
# U+2212 is the true minus sign; the rest are what word processors and copy-paste actually emit.
# A claim's sign is the difference between a collapse and a recovery, so none of these may be dropped.
DASHES = "-‐‑‒–—―−﹘﹣－"
PCT_RE = re.compile(rf"([+{DASHES}]?\d+(?:[.,]\d+)?)\s*%")
DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# a phone number has ≥9 digits in total; "120 - 400" style numeric ranges do not
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
WS_RE = re.compile(r"\s+")

# money: a currency symbol or ISO code is required, before or after the number; k/M multipliers count only
# next to a currency ("90M rows", "18k row cap", "22m" are volumes and durations, not money)
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW"}
CURRENCY_CODES = ("USD", "EUR", "GBP", "JPY", "INR", "KRW", "CHF", "CAD", "AUD", "SEK")
_NUM = r"\d{1,3}(?:[,.]\d{2,3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d+)?"
# derived from CURRENCY_SYMBOLS, never re-listed: a symbol added above must not need a second edit here
_CUR = "[" + re.escape("".join(CURRENCY_SYMBOLS)) + "]|" + "|".join(CURRENCY_CODES)
MONEY_RE = re.compile(
    rf"(?:(?P<cur>{_CUR})\s?(?P<num>{_NUM})\s?(?P<mult>[kKmM])?(?![\w.,]\w))"
    rf"|(?:(?<![\w.,])(?P<num2>{_NUM})\s?(?P<mult2>[kKmM])?\s?(?P<cur2>{_CUR})(?![A-Za-z]))")

try:
    import phonenumbers as _phonenumbers
    PHONE_BACKEND = "phonenumbers"
except ImportError:  # the regex fallback is coarser: any ≥9-digit run with phone punctuation
    _phonenumbers = None
    PHONE_BACKEND = "regex"


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
    """Signed percentage out of a claim string like 'api_calls -61% week over week'.

    Any unicode dash counts as a minus: the agent's text is prose, and a word processor turning "-40%"
    into "−40%" must not read as +40%. The separator inside the number goes through _amount, so a locale
    writing "61,5%" means 61.5 while "1,234%" still means 1234."""
    m = PCT_RE.search(claim or "")
    if not m:
        return None
    raw = m.group(1)
    sign = -1.0 if raw[0] in DASHES else 1.0
    return sign * _amount(raw.lstrip("+" + DASHES))


def dollars(text):
    m = DOLLAR_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _amount(num):
    """'1,200' → 1200; '1.200,50' → 1200.5; '4.50' → 4.5; '2,50,000' → 250000. The last separator is the
    decimal mark when it is followed by 1–2 digits; every other separator groups thousands."""
    parts = re.split(r"([,.])", num)
    digits, seps = parts[0::2], parts[1::2]
    if not seps:
        return float(num)
    if len(digits[-1]) <= 2:
        return float("".join(digits[:-1]) + "." + digits[-1])
    return float("".join(digits))


def money(text):
    """(amount, currency) of the FIRST currency amount in the text, or None. Syntactic: a symbol or ISO code
    must sit next to the number; bare numbers and bare k/M multipliers are never money."""
    m = MONEY_RE.search(text or "")
    if not m:
        return None
    cur = m.group("cur") or m.group("cur2")
    num = m.group("num") or m.group("num2")
    mult = m.group("mult") or m.group("mult2")
    amount = _amount(num) * {"k": 1e3, "m": 1e6}.get((mult or "").lower(), 1)
    return amount, CURRENCY_SYMBOLS.get(cur, cur)


# Regions a bare (no '+') number may be written for. Known list-based limit: a national-format number
# from another region is not recognised as a contact detail. International '+…' numbers need no region.
PHONE_REGIONS = ("US", "GB", "DE", "IN", "JP", "BR", "ES", "FR")


def has_phone(text):
    """Does the text carry a phone number? With `phonenumbers`: a valid international number, or a bare number
    that is valid in one of PHONE_REGIONS — so invoice ids, ISO timestamps and numeric ranges never count.
    Without the library: the coarser PHONE_RE (≥9 digits with phone punctuation)."""
    text = text or ""
    if _phonenumbers is None:
        return any(sum(ch.isdigit() for ch in m.group(0)) >= 9 for m in PHONE_RE.finditer(text))
    if not any(ch.isdigit() for ch in text):
        return False
    regions = (None,) + PHONE_REGIONS if "+" in text else PHONE_REGIONS
    for region in regions:
        try:
            if any(True for _ in _phonenumbers.PhoneNumberMatcher(text, region, leniency=_phonenumbers.Leniency.VALID)):
                return True
        except Exception:  # library-internal parse failure on odd input: not a phone
            continue
    return False


def split_quoted(text):
    """(head, tail, marker) — a derived view of text.blocks(): head is the current text (depth-0 content
    blocks), tail is everything else (quoted / forwarded material and the signature), marker names the first
    quote convention seen or None. ('', text, None) never happens: an all-quoted text has head ''."""
    from .text import blocks  # text.py builds on util; the view is the one dependency the other way
    text = text or ""
    bs = blocks(None, text)
    head = "\n".join(b.text for b in bs if b.depth == 0 and not b.is_signature)
    tail = "\n".join(b.text for b in bs if b.depth != 0 or b.is_signature)
    first_quoted = next((b for b in bs if b.depth != 0), None)
    marker = None
    if first_quoted is not None:
        line = first_quoted.text.lstrip("\n").split("\n")[0]
        marker = ("On … wrote:" if re.match(r"On .{3,160}? wrote:", line) else
                  "forwarded/original message" if line.startswith("-") else
                  ">" if line.startswith(">") else "pasted header")
    return (head.rstrip() if tail else head), tail, marker


# Quote characters by unicode category: Pi (initial), Pf (final) and the modifier apostrophes that
# editors substitute for ' and ". Built once from the categories rather than listed by hand, so a
# variant nobody thought of still folds.
_QUOTE_MAP = {ord(c): "'" for c in "‘’‚‛‹›ʻʼʽˈ"}
_QUOTE_MAP.update({ord(c): '"' for c in "“”„‟«»"})
_QUOTE_MAP.update({ord(c): "-" for c in DASHES})


def norm(s):
    """Typographic normalisation for the I6 near-match: NFKC folds compatibility forms (… → ..., fullwidth
    → ASCII, ligatures), then every dash and quote variant maps to its ASCII form, then whitespace and case.

    This decides whether a quote was fabricated — spec §7's most serious finding — so a sentence that
    differs from its artefact only by an ellipsis character must not read as invented evidence. Accents are
    deliberately left alone: 'café' and 'cafe' are different words, and folding them would weaken the check
    rather than strengthen it."""
    folded = unicodedata.normalize("NFKC", s or "").translate(_QUOTE_MAP)
    return WS_RE.sub(" ", folded).strip().lower()


def author_class(author_type):
    """'customer' | 'internal' | 'bot' | None, case- and whitespace-tolerant.

    None means unknown — either the field was absent or it carried a value outside the vocabulary. Both
    deserve the same caution: an export writing 'Customer' or 'end_user' must not read as positive evidence
    that a human customer did NOT write the text, which is what an exact-match comparison concludes."""
    from .spec import AUTHOR_TYPES
    if not isinstance(author_type, str):
        return None
    v = author_type.strip().lower()
    return v if v in AUTHOR_TYPES else None


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
    """Did a human get this signal? Notified, routed, or a human pre-empted it (spec §4.5: a person
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
