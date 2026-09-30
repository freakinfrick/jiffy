"""`{{token}}` handling at the buffer level.

The tldr corpus ships lines like `find {{path}} -name '{{pattern}}'`. Accepting
one parks the cursor on the first token and TAB walks the rest; a line that
still carries a token is never run — not by ENTER, not by the validator, not by
`shell.execute`. Three independent refusals, because "it ran a template" is the
failure the user would never forgive.
"""
from __future__ import annotations

from jiffy.graph.assemble import PLACEHOLDER

__all__ = ["spans", "has_placeholder", "next_span", "first_span", "span_at"]


def spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every `{{...}}` token, in order."""
    return [(m.start(), m.end()) for m in PLACEHOLDER.finditer(text)]


def has_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER.search(text))


def first_span(text: str) -> tuple[int, int] | None:
    found = spans(text)
    return found[0] if found else None


def span_at(text: str, cursor: int) -> tuple[int, int] | None:
    """The token the cursor is sitting in or against, if any.

    Inclusive of both ends: the cursor parks at the START of a token when a line
    is accepted, and a user who has just walked to one with TAB is at its start
    too. Typing there replaces the whole token rather than inserting into it,
    which is the only reason this function exists.
    """
    for start, end in spans(text):
        if start <= cursor <= end:
            return start, end
    return None


def next_span(text: str, cursor: int) -> tuple[int, int] | None:
    """The token after `cursor`, wrapping to the first. None when there are none."""
    found = spans(text)
    if not found:
        return None
    for start, end in found:
        if start > cursor:
            return start, end
    return found[0]
