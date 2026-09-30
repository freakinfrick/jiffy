"""Assembly for the frontend: sanitise the user's words, then delegate.

The command string is built by `jiffy.graph.assemble` and nowhere else (SPEC
module map) — this module adds no second assembler, and `assemble()` below is a
one-line call into it.

What it *does* own is the boundary: the request is the only untrusted text in
the shipped line, and `jiffy.graph.assemble` interpolates it verbatim. A `#` in
it would open a second comment and a newline would open a second SHELL LINE:

    ls  # list files
    rm -rf /tmp/x            <- what the user typed after a newline

So the request is normalised here, where the user's typing enters the system:
control characters and newlines collapse to spaces, `#` is dropped, and the
result is a single-line comment on a single-line command. The command half is
untouched.
"""
from __future__ import annotations

import re

from jiffy.graph.assemble import PLACEHOLDER, Assembled, assemble as _assemble

__all__ = ["assemble", "sanitise", "Assembled", "PLACEHOLDER"]

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def sanitise(request: str) -> str:
    """The user's words, reduced to something that cannot leave the comment.

    Lossy by exactly two characters: `#` (would open a second comment) and any
    control character (a newline would open a second command). Everything else —
    quotes, semicolons, backticks, `$()` — is inert inside a trailing comment and
    is preserved verbatim.
    """
    return " ".join(_CONTROL.sub(" ", request).replace("#", "").split())


def assemble(line: str, request: str, fills: tuple = ()) -> Assembled:
    """`line  # request`, built by jiffy.graph.assemble from a sanitised request.

    `fills` is carried through untouched: it describes the line, which this
    function does not alter, so sanitising the request cannot invalidate it.
    """
    return _assemble(line, sanitise(request), fills)
