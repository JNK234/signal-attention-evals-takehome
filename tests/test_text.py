"""
ABOUTME: Known-answer tests for the block layer (signal_eval.text) and the syntactic helpers it relies on
ABOUTME: (util.money / util.has_phone). Corpus artefacts are regression inputs; the rules are stated without them.
"""

import json
from pathlib import Path

import pytest

from signal_eval.text import Block, blocks, find_quote, is_stale
from signal_eval.util import has_phone, money, split_quoted

DATA = Path(__file__).resolve().parents[1] / "data" / "artifacts.jsonl"
needs_corpus = pytest.mark.skipif(not DATA.exists(), reason="corpus not present")


def corpus():
    with open(DATA) as f:
        return [json.loads(line) for line in f]


def art(aid):
    return next(a for a in corpus() if a["artifact_id"] == aid)


def content(bs):
    """The blocks a labeller reads: everything that is not a signature."""
    return [b for b in bs if not b.is_signature]


def depths(bs):
    return [b.depth for b in content(bs)]


# ── block boundaries on verbatim corpus text ────────────────────────────────
@needs_corpus
def test_top_posted_reply_is_two_blocks_and_signature_is_excluded():
    """art_02546: current reply, 'On … wrote:' + '>' history, then 'Best,\\nKenji Iyer\\n<email> | <phone>'."""
    a = art("art_02546")
    bs = blocks(a["subject"], a["text"])
    assert depths(bs) == [0, 1]
    head, tail = content(bs)
    assert head.text.startswith("Holiday coverage\nSorted, thanks.")        # subject joined onto block 0
    assert "Third time this week" in tail.text and "Third time this week" not in head.text
    sig = [b for b in bs if b.is_signature]
    assert len(sig) == 1 and "kenji@ravensworth.com" in sig[0].text and "Kenji Iyer" in sig[0].text
    assert not any("kenji@" in b.text for b in content(bs))


@needs_corpus
def test_forwarded_message_is_a_depth_one_block():
    """art_01511: internal note, then '---------- Forwarded message ----------' naming another account."""
    a = art("art_01511")
    bs = blocks(a["subject"], a["text"])
    assert depths(bs) == [0, 1]
    assert bs[0].text.startswith("champion moved to a new team")
    assert "Brightwater Systems gave notice" in bs[1].text and "gave notice" not in bs[0].text


def test_nested_quote_reaches_depth_two():
    text = ("Thanks, agreed.\n\nOn 2 Mar 2026, Ana wrote:\n> Fine by me.\n> On 1 Mar 2026, Ben wrote:\n"
            "> > We are cancelling at term end.")
    bs = blocks(None, text)
    assert depths(bs) == [0, 1, 2]
    assert "cancelling" in bs[2].text and "Fine by me" in bs[1].text and "cancelling" not in bs[1].text


@needs_corpus
def test_signature_after_quote_is_not_a_content_block():
    """art_02102: the trailing 'Best,' block sits after the '>' history; without signature detection the
    return to depth 0 would present it as a second current block."""
    a = art("art_02102")
    bs = blocks(a["subject"], a["text"])
    assert depths(bs) == [0, 1]
    assert bs[-1].is_signature and "lucas@pelham.com" in bs[-1].text


def test_bottom_posted_reply_is_history_then_current():
    text = "On 1 Mar 2026, Ben wrote:\n> Loads are taking 60s again.\n\nAll fixed now, thanks for the patience."
    bs = blocks(None, text)
    assert depths(bs) == [1, 0]
    assert "All fixed now" in bs[1].text and "60s" not in bs[1].text


@needs_corpus
def test_newline_subject_joins_the_first_block():
    """art_03049: subject 'Hallo,\\nHow do I …' and a 'Guten Tag,' body form one block."""
    a = art("art_03049")
    bs = blocks(a["subject"], a["text"])
    assert len(content(bs)) == 1
    assert bs[0].text.startswith("Hallo,\nHow do I change the timezone") and "Guten Tag" in bs[0].text


@needs_corpus
def test_foreign_greeting_and_closing_around_english_body_is_one_block():
    """art_01843: 'Guten Tag,' + English body + 'Mit freundlichen Grüßen' — the closing is a signature line."""
    a = art("art_01843")
    bs = blocks(a["subject"], a["text"])
    assert len(content(bs)) == 1 and "override the default palette" in bs[0].text
    assert bs[-1].is_signature and bs[-1].text.strip() == "Mit freundlichen Grüßen"


@needs_corpus
def test_emoji_is_kept_verbatim():
    a = art("art_03476")
    bs = blocks(a["subject"], a["text"])
    assert len(bs) == 1 and bs[0].text == a["text"] and "🙃" in bs[0].text


def test_pasted_header_block_starts_quoted_material():
    text = "Passing this on.\n\nFrom: Ben\nSent: Monday\nSubject: notice\nWe will not renew."
    bs = blocks(None, text)
    assert depths(bs) == [0, 1] and "not renew" in bs[1].text


def test_blocks_carry_spans_into_the_document():
    a = {"subject": "Re: sync", "text": "All good.\n\n> old complaint"}
    bs = blocks(a["subject"], a["text"])
    doc = a["subject"] + "\n" + a["text"]
    assert all(doc[b.span[0]:b.span[1]] == b.text for b in bs)
    assert isinstance(bs[0], Block)


# ── find_quote ──────────────────────────────────────────────────────────────
THREAD = "Sorted, thanks. Ignore the thread below.\n\nOn 20 Sep 2025, Kenji Iyer wrote:\n> Third time this week, we are done with this vendor."


def test_find_quote_head_tail_both_none():
    bs = blocks(None, THREAD)
    assert find_quote("Sorted, thanks. Ignore the thread below.", bs) == "head"
    assert find_quote("Third time this week, we are done with this vendor.", bs) == "tail"
    assert find_quote("we are done", blocks(None, "we are done\n\n> we are done")) == "both"
    assert find_quote("not in the text", bs) is None
    assert find_quote("", bs) is None


# ── split_quoted stays a derived view of the blocks ─────────────────────────
def test_split_quoted_is_head_and_the_rest():
    head, tail, marker = split_quoted(THREAD)
    assert head == "Sorted, thanks. Ignore the thread below." and tail.startswith("On 20 Sep 2025") and marker
    text = "Just a plain ticket."
    assert split_quoted(text) == (text, "", None)
    head, tail, marker = split_quoted("Body line.\n\nBest,\nAda\nada@example.com")
    assert head == "Body line." and "ada@example.com" in tail and marker is None


# ── is_stale (moved from classifier.py; same semantics) ─────────────────────
def test_is_stale_reads_old_label_dicts_and_new_readings():
    assert is_stale(None, "customer") is False
    assert is_stale({"unverifiable": True}, "customer", quote_in_tail=True) is True
    assert is_stale({"unverifiable": False, "sarcasm": True}, "customer") is True
    assert is_stale({"unverifiable": False, "sarcasm": True}, "internal") is False
    reading = {"verdict": {"sarcasm": True}, "unreadable": []}
    assert is_stale(reading, None) is True and is_stale(reading, "internal") is False


# ── money: a currency is required; multipliers only with a currency ─────────
@pytest.mark.parametrize("text,expect", [
    ("We are planning a backfill of about 90M rows.", None),
    ("the 18k row cap", None),
    ("took 22m end to end", None),
    ("1.2M events per day", None),
    ("order 123456789 shipped", None),
    ("Invoice INV-9 for $1,200 issued.", (1200.0, "USD")),
    ("€12k for the add-on", (12000.0, "EUR")),
    ("USD 1.2M over three years", (1200000.0, "USD")),
    ("£4.50 per seat", (4.5, "GBP")),
    ("¥1.200.000 im Jahr", (1200000.0, "JPY")),
    ("1.200,50 EUR offen", (1200.5, "EUR")),
    ("5k USD credit", (5000.0, "USD")),
    ("INR 2,50,000 quoted", (250000.0, "INR")),
    ("first $300 then $9,000", (300.0, "USD")),
    ("", None), (None, None),
])
def test_money_requires_a_currency(text, expect):
    assert money(text) == expect


# ── has_phone: contact details, not identifiers ─────────────────────────────
@pytest.mark.parametrize("text,expect", [
    ("INV-123456789", False),
    ("2026-03-02T08:00:00Z", False),
    ("order 123456789", False),
    ("Team is out 120 - 400 for the holiday.", False),
    ("kenji@ravensworth.com | +1-415-645-1761", True),
    ("Rückruf unter +49 30 1234 5678", True),
    ("", False), (None, False),
])
def test_has_phone(text, expect):
    assert has_phone(text) is expect


# ── nothing is discarded ────────────────────────────────────────────────────
@needs_corpus
def test_nothing_is_discarded():
    def squash(s):
        return "".join((s or "").split())
    for a in corpus():
        bs = blocks(a.get("subject"), a.get("text"))
        assert "".join(squash(b.text) for b in bs) == squash(a.get("subject")) + squash(a.get("text")), a["artifact_id"]
        assert all(b.depth >= 0 for b in bs)
