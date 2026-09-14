"""
ABOUTME: Known-answer tests for the parsing helpers — percentages, quote normalisation, money. Every
ABOUTME: expectation here is derived from what the string means, never from what the implementation returns.
"""

import pytest
from signal_eval.util import money, norm, pct

# The characters a writer, a word processor or a locale may use where a programmer would type "-".
# U+2212 is the real minus sign; the dashes are what autocorrect and copy-paste actually produce.
MINUS_VARIANTS = [
    ("-", "ASCII hyphen-minus U+002D"),
    ("−", "minus sign U+2212"),
    ("–", "en dash U+2013"),
    ("‐", "hyphen U+2010"),
    ("‑", "non-breaking hyphen U+2011"),
]


# ── pct: a claim's sign is the difference between a collapse and a recovery ────

@pytest.mark.parametrize("dash,name", MINUS_VARIANTS)
def test_a_negative_percentage_is_negative_whichever_dash_was_typed(dash, name):
    """-61% is a decline. Which of the five unicode dashes the author used cannot change that."""
    assert pct(f"api_calls {dash}61% week over week") == -61.0, name


@pytest.mark.parametrize("text,expect", [
    ("api_calls -61% wow", -61.0),
    ("api_calls +61% wow", 61.0),
    ("api_calls 61% wow", 61.0),
    ("dau_seats -7.5% since May", -7.5),
    ("error_rate 0%", 0.0),
    ("-100%", -100.0),
    ("usage fell 61 %", 61.0),            # detached percent sign
    ("no numbers here", None),
    ("", None),
    (None, None),
])
def test_pct_reads_the_number_that_was_written(text, expect):
    assert pct(text) == expect


def test_pct_takes_the_first_percentage_when_several_are_present():
    assert pct("api_calls -61% and dau_seats -12%") == -61.0


@pytest.mark.parametrize("text,expect", [
    ("-61,5%", -61.5),      # decimal comma: one comma, two trailing digits
    ("61,5%", 61.5),
    ("-7,5%", -7.5),
])
def test_a_decimal_comma_is_a_decimal_point(text, expect):
    """A locale that writes 61,5 means sixty-one and a half, not sixty-one."""
    assert pct(text) == expect


@pytest.mark.parametrize("text,expect", [
    ("1,234%", 1234.0),          # three trailing digits: a thousands separator
    ("12,345%", 12345.0),
])
def test_a_thousands_separator_is_not_a_decimal_point(text, expect):
    assert pct(text) == expect


# ── norm: used to decide whether a quote was fabricated ───────────────────────

def test_norm_leaves_plain_ascii_alone_apart_from_case_and_spacing():
    assert norm("We are not renewing.") == "we are not renewing."


@pytest.mark.parametrize("dash,name", MINUS_VARIANTS)
def test_every_dash_variant_normalises_to_the_same_string(dash, name):
    assert norm(f"down {dash}5% this week") == norm("down -5% this week"), name


@pytest.mark.parametrize("fancy,plain", [
    ("we said… maybe", "we said... maybe"),            # ellipsis U+2026
    ("it’s fine", "it's fine"),                        # right single quote
    ("itʼs fine", "it's fine"),                        # modifier letter apostrophe
    ("“quoted”", '"quoted"'),                     # curly double quotes
    ("‘quoted’", "'quoted'"),                     # curly single quotes
    ("ＦＩＸ", "fix"),                          # fullwidth letters
    ("a b", "a b"),                                    # non-breaking space
    ("line\r\nbreak", "line break"),                        # CRLF
])
def test_typographic_variants_do_not_make_a_quote_look_fabricated(fancy, plain):
    """These pairs are the same sentence. Treating them as different reports a real quote as invented."""
    assert norm(fancy) == norm(plain)


def test_accents_are_preserved_because_they_change_the_word():
    """cafe and cafe-with-an-accent are different strings; folding them would weaken the I6 check."""
    assert norm("café meeting") != norm("cafe meeting")


@pytest.mark.parametrize("value", ["", None])
def test_norm_of_nothing_is_the_empty_string(value):
    assert norm(value) == ""


def test_norm_is_idempotent():
    """Normalising an already-normalised string must not change it again."""
    for s in ["We are not renewing…", "it’s “fine”", "a b–c", "plain text", ""]:
        assert norm(norm(s)) == norm(s)


# ── money: an amount the evaluator cannot price must not read as no amount ─────

@pytest.mark.parametrize("text,expect", [
    ("disputed ₹500000", (500000.0, "INR")),    # rupee sign; INR was already a known code
    ("disputed INR 500000", (500000.0, "INR")),
    ("disputed $5000", (5000.0, "USD")),
    ("disputed €12k", (12000.0, "EUR")),
])
def test_money_reads_the_currency_that_was_written(text, expect):
    assert money(text) == expect


def test_a_number_with_no_currency_is_not_money():
    assert money("a backfill of 90M rows") is None
    assert money("we waited 22m") is None
