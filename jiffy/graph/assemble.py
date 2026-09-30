"""CODE assembles the command string. The judge only ever picks.

One function, because there is exactly one shipped form (spec, "Interaction
contract"):

    find . -name '*.log' -size +100M  # find big log files
"""
from __future__ import annotations

from jiffy.fill.tokens import PLACEHOLDER
from jiffy.types import Assembled, Fill

__all__ = ["assemble", "placeholders", "PLACEHOLDER"]


def placeholders(line: str) -> list[str]:
    """`{{...}}` tokens, in the order they appear. The frontend parks the cursor on the first."""
    return PLACEHOLDER.findall(line)


def sanitise_request(request: str) -> str:
    """Make `request` safe to interpolate into a trailing shell comment.

    The request is raw user text. A newline in it would end the comment and
    start a second command; a `#` would open a comment inside the comment.
    Collapsing whitespace and dropping `#` keeps the assembled form a single
    shell command whatever the user typed. Sanitising HERE, at the only place
    that builds the comment, is what makes that true for every caller.
    """
    return " ".join(request.replace("#", "").split())


def assemble(line: str, request: str, fills: tuple[Fill, ...] = ()) -> Assembled:
    """Bare documented line + the user's words -> the shipped form.

    `fills` is carried, not applied: the line arrives already filled, and this
    records who filled each token so the prompt can colour it.
    """
    line = line.strip()
    request = sanitise_request(request)
    comment = f"{line}  # {request}" if request and line else line
    return Assembled(line, request, comment, placeholders(line), fills)
