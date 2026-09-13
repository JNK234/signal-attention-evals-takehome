"""
ABOUTME: The block layer. Splits an artefact only at quote boundaries into blocks (maximal runs of lines at
ABOUTME: one quote depth) and tags trailing signatures; nothing is discarded, no sentence splitting, no meaning read.
"""

import re
from dataclasses import dataclass

from .util import EMAIL_RE, has_phone

# Known limit: closings and quote headers are list-based. A closing not listed here stays in the content
# block (harmless for labels: one more line of text); a quote convention not listed reads as current text.
CLOSING_RE = re.compile(
    r"^(?:Best(?: regards| wishes)?|(?:Kind |Warm )?[Rr]egards|Thanks(?: again)?|Thank you|Cheers|"
    r"Mit freundlichen Grüßen|Viele Grüße|Saludos|Atentamente|Atenciosamente|Abraço|"
    r"よろしくお願いいたします|よろしくお願いします)[,!.]?\s*$")
SIGNATURE_MAX_LINES = 4
SIGNATURE_MAX_TOKENS = 4

# Quote headers: a line that announces quoted/forwarded material below it. Syntactic, never tonal.
HEADER_RES = [
    re.compile(r"^On .{3,160}? wrote:\s*$"),                              # "On 20 Sep 2025, Kenji Iyer wrote:"
    re.compile(r"^-{2,}\s*(?:Original|Forwarded) message\s*-{2,}\s*$", re.I),
]
FROM_RE = re.compile(r"^(?:From|Von|De):\s", re.I)                        # pasted header block: From: … then
SENT_RE = re.compile(r"^(?:Sent|To|Date|An|Para):\s", re.I)               # Sent:/To:/Date:/An:/Para:
QUOTE_PREFIX_RE = re.compile(r"^((?:>\s?)+)")
MAX_BLOCK_CHARS = 1500    # ~ the model's 512-token budget; a longer block is scored truncated and flagged


@dataclass(frozen=True)
class Block:
    depth: int
    text: str
    is_signature: bool
    span: tuple


def document(subject, text):
    """The string every span indexes into: subject lines, then the body."""
    subject, text = subject or "", text or ""
    return f"{subject}\n{text}" if subject else text


def _signature_start(lines):
    """Index of the first signature line, or None. Trailing group of ≤ SIGNATURE_MAX_LINES non-blank lines that
    starts with a closing and continues only with contact lines (email/phone) or ≤ 4-token lines (a name)."""
    idx = [i for i, l in enumerate(lines) if l.strip()]
    tail = idx[-SIGNATURE_MAX_LINES:]
    for k, i in enumerate(tail):
        if not CLOSING_RE.match(lines[i].strip()):
            continue
        rest = [lines[j].strip() for j in tail[k + 1:]]
        if all(EMAIL_RE.search(l) or has_phone(l) or len(l.split()) <= SIGNATURE_MAX_TOKENS for l in rest):
            return i
    return None


def _unprefix(line):
    """(number of '>' markers, the line after them)."""
    m = QUOTE_PREFIX_RE.match(line)
    return (m.group(1).count(">"), line[m.end():]) if m else (0, line)


def _is_header(body, next_body):
    return any(rx.match(body) for rx in HEADER_RES) or bool(FROM_RE.match(body) and next_body is not None and SENT_RE.match(next_body))


def _tag_depths(lines):
    """Quote depth per body line. `base` is the depth of un-prefixed text: 0, or +1 after a header whose quoted
    material carries no '>' markers (forwarded/pasted blocks run to the end of the text). A header followed by
    '>' lines is consumed by them: the '>' count is the depth, and the first un-prefixed line returns to `base`."""
    depths, base, pending, prev = [], 0, None, 0
    for i, raw in enumerate(lines):
        n, body = _unprefix(raw)
        if not raw.strip():                        # blank: stays with the preceding line's block
            depths.append(prev)
            continue
        if pending is not None:                    # first non-blank line after a header
            if n:                                  # the header's quote is the '>' block
                base = pending
            pending = None
        depth = base + n
        if _is_header(body.strip(), _unprefix(lines[i + 1])[1].strip() if i + 1 < len(lines) else None):
            depth += 1                             # the header line belongs to the material it introduces
            pending, base = base, depth            # un-prefixed lines below are quoted unless '>' takes over
        depths.append(depth)
        prev = depth
    return depths


def blocks(subject, text):
    """[Block] — the artefact split at quote boundaries only. Subject lines are depth-0 lines placed before the
    body (they merge with the body's first block when that is at depth 0). Signature detection runs first on
    the body so a trailing signature never reads as a second current block after quoted material."""
    subject, text = subject or "", text or ""
    doc = document(subject, text)
    body_lines = text.split("\n")
    subj_lines = subject.split("\n") if subject else []
    sig_at = _signature_start(body_lines) if text.strip() else None
    tagged = [(0, False, l) for l in subj_lines]
    for i, (d, l) in enumerate(zip(_tag_depths(body_lines), body_lines)):
        tagged.append((d, sig_at is not None and i >= sig_at, l))
    out, pos, i = [], 0, 0
    while i < len(tagged):
        d, sig, _ = tagged[i]
        j = i
        while j < len(tagged) and tagged[j][0] == d and tagged[j][1] == sig:
            j += 1
        chunk = "\n".join(l for _, _, l in tagged[i:j])
        start = pos
        end = start + len(chunk)
        assert doc[start:end] == chunk
        out.append(Block(d, chunk, sig, (start, end)))
        pos = end + 1                              # the newline joining this block to the next
        i = j
    return out


def find_quote(quote, bs):
    """Where a verbatim quote sits: "head" (a current, depth-0 content block), "tail" (quoted / forwarded
    material at depth ≥ 1), "both", or None when empty or absent. A quote that runs across a depth-0 block and
    its signature is still current text, so the joined depth-0 text is the fallback for "head"."""
    if not quote:
        return None
    in_head = any(quote in b.text for b in bs if b.depth == 0 and not b.is_signature)
    if not in_head:
        in_head = quote in "\n".join(b.text for b in bs if b.depth == 0)
    in_tail = any(quote in b.text for b in bs if b.depth >= 1)
    if in_head and in_tail:
        return "both"
    return "head" if in_head else "tail" if in_tail else None


def is_stale(reading_or_label, author_type=None, quote_in_tail=False):
    """Is the operative content not current evidence? Stale when the agent's quote was lifted from the
    quoted/forwarded tail of the artefact (docs/domain.md 'Quoted history': the new message is current,
    'the quoted text below it repeats an alarming complaint from months earlier') — a structural fact that
    needs no model — or when the head is a joke written by the customer (docs/domain.md 'Sarcasm': 'chat
    messages from friendly champions'). Internal notes with 'lol' are casual, not sarcastic evidence to
    discount. Accepts a Reading (`verdict`) or the old-style label dict; a quoted tail alone is informational:
    an artefact with a quoted tail is still current evidence when the agent quoted its head."""
    if quote_in_tail:
        return True
    lab = reading_or_label
    if not lab:
        return False
    if "verdict" in lab:
        sarcasm = lab["verdict"].get("sarcasm") is True
    else:
        if lab.get("unverifiable"):
            return False
        sarcasm = bool(lab.get("sarcasm"))
    return sarcasm and author_type in ("customer", None)
