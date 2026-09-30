"""What tldr means by a `{{...}}`, and the rule that a spelling is not a value."""
from __future__ import annotations

import pytest

from jiffy.fill.tokens import Kind, parse, shortest


@pytest.mark.parametrize("raw,short", [
    ("[-s|--summarize]", "-s"),
    ("[f|filesystem]", "f"),
    ("[st|status]", "st"),
    ("[-r|--recursive]", "-r"),
])
def test_an_alternation_is_a_spelling_not_a_value(raw, short):
    """Both spellings already work: nothing is missing, so nothing is asked of the user."""
    token = parse(raw)
    assert token.spelling
    assert shortest(token.options) == short


def test_a_two_shape_value_is_still_a_value():
    """`{{name|uuid}}` has no brackets: it wants a value, in one of two shapes."""
    assert not parse("name|uuid").spelling


@pytest.mark.parametrize("raw,kind", [
    ("path/to/directory", Kind.DIR),
    ("chip", Kind.CHIP),
    ("chips", Kind.CHIP),
    ("delimiter", Kind.DELIM),
    ("field_delim", Kind.DELIM),
    ("separator", Kind.DELIM),
    ("field", Kind.UNKNOWN),                 # tldr names fields by name (`mlr sort -f {{field}}`)
    ("path/to/file", Kind.FILE),
    ("path/to/file_or_directory", Kind.FILE),
    ("directory", Kind.DIR),
    ("filename", Kind.FILE),
    ("username", Kind.USER),
    ("hostname", Kind.HOST),
    ("port", Kind.PORT),
    ("*.ext", Kind.PATTERN),
    ("pattern", Kind.PATTERN),
    ("regex", Kind.PATTERN),
    ("depth", Kind.NUMBER),
    ("wibble", Kind.UNKNOWN),
])
def test_a_token_is_classified_by_what_tldr_called_it(raw, kind):
    assert parse(raw).kind is kind


def test_an_unknown_token_is_not_guessed_at():
    """UNKNOWN has no source that answers it, which is how it stays unfilled."""
    assert parse("wibble").kind is Kind.UNKNOWN


@pytest.mark.parametrize("raw,kind", [
    ("num", Kind.NUMBER),          # `tree -L {{num}}` — the case from the pane
    ("n", Kind.NUMBER),
    ("level", Kind.NUMBER),
    ("width", Kind.NUMBER),
    ("process_id", Kind.NUMBER),
    ("url", Kind.URL),
    ("https://example.com", Kind.URL),
])
def test_short_and_common_tokens_are_recognised(raw, kind):
    assert parse(raw).kind is kind


@pytest.mark.parametrize("raw", ["report", "filename_prefix", "rename", "portal"])
def test_a_rule_matches_a_word_not_a_substring(raw):
    """`report` is not a port, `rename` is not a name. Substring matching filled both."""
    assert parse(raw).kind is not Kind.PORT or raw == "port"
    assert parse("report").kind is Kind.UNKNOWN
    assert parse("rename").kind is Kind.UNKNOWN


def test_an_optional_token_is_marked_and_classified_without_its_mark():
    t = parse("?path/to/directory")
    assert t.optional and t.raw == "path/to/directory" and t.kind is Kind.DIR
    assert not parse("path/to/directory").optional
