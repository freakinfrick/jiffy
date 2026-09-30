"""Colour for the line you are editing.

prompt_toolkit styles a buffer through a `Lexer`, so this is the only way the
same colours that mark up the candidate list can reach the text under the
cursor. It re-uses `render.fragments`, so there is one definition of what a
filled value looks like, not two that drift.

The fill spans are byte offsets into the line the graph produced. The moment you
edit the line they stop being true, so they are used only while the buffer still
holds exactly that line; after an edit the unresolved `{{...}}` tokens are still
found by pattern, because those are visible in the text itself. Colour degrades;
it never lies about where a value came from.
"""
from __future__ import annotations

from typing import Callable

from prompt_toolkit.lexers import Lexer

from jiffy.fill.tokens import PLACEHOLDER
from jiffy.types import Fill

from . import render

__all__ = ["PlaceholderLexer", "spans_for"]


def spans_for(text: str, assembled) -> tuple[Fill, ...]:
    """The fills that still describe `text`, or just its unresolved tokens.

    An untouched line gets the full account — which value came from the request,
    which was inferred. An edited one falls back to what the text still proves:
    a `{{token}}` is unresolved wherever it appears.
    """
    if assembled is not None and text == assembled.line and assembled.fills:
        return tuple(assembled.fills)
    return tuple(Fill(m.group(1), "", "", "", m.start(), m.end())
                 for m in PLACEHOLDER.finditer(text))


class PlaceholderLexer(Lexer):
    """Lexes the prompt buffer against whatever the engine last answered."""

    def __init__(self, assembled_for: Callable[[], object]) -> None:
        self.assembled_for = assembled_for

    def lex_document(self, document):
        lines = document.lines

        def get_line(i: int):
            text = lines[i]
            if not text:
                return []
            try:
                assembled = self.assembled_for()
            except Exception:
                assembled = None
            return render.fragments(text, spans_for(text, assembled))

        return get_line
