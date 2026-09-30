"""Terminal hygiene: the prompt must never draw over the shell's last line.

Driven against a small screen model rather than a pty, so it is deterministic
and costs a millisecond. `screen.Screen` implements the handful of sequences
prompt_toolkit emits; `render.settle` is the string under test.
"""
from __future__ import annotations

import pytest

from jiffy.tui import render

from screen import Screen

COLS = 40
PROMPT = "» "


def _after(output: str) -> Screen:
    """A command's output, then the settle, then the next prompt drawing."""
    scr = Screen(rows=8, cols=COLS)
    scr.feed(_tty(output))
    scr.feed(render.settle(COLS))
    scr.feed(PROMPT)
    return scr


def _tty(text: str) -> str:
    """What a pty actually delivers: ONLCR turns every newline into CR LF."""
    return text.replace("\n", "\r\n")


def _rows(scr: Screen) -> list[str]:
    return [r for r in scr.visible if r]


def test_a_last_line_without_a_newline_survives_the_next_prompt():
    """The bug: `printf 'x'` leaves the cursor mid-row and the prompt overwrites it."""
    scr = _after("keep-A\nkeep-B\nno-newline-here")
    assert "no-newline-here" in _rows(scr)
    assert _rows(scr)[-1] == PROMPT.strip()


def test_output_that_ends_cleanly_gains_no_blank_line():
    """Whitespace is intentional: a newline nobody asked for is a bug too."""
    scr = _after("clean-A\nclean-B\n")
    assert _rows(scr) == ["clean-A", "clean-B", PROMPT.strip()]


def test_without_settle_the_partial_line_is_destroyed():
    """Proves the test can see the failure it guards against."""
    scr = Screen(rows=8, cols=COLS)
    scr.feed(_tty("keep-A\nno-newline-here"))
    scr.feed(PROMPT)
    assert "no-newline-here" not in _rows(scr)


@pytest.mark.parametrize("tail", ["x", "x" * (COLS - 1), "x" * COLS, "x" * (COLS + 3)])
def test_a_partial_line_of_any_width_survives(tail):
    """Including one that exactly fills the row, where the cursor may already have wrapped."""
    scr = _after(f"before\n{tail}")
    assert tail[:COLS] in "".join(_rows(scr))


def test_settle_leaves_the_cursor_at_column_zero():
    scr = Screen(rows=8, cols=COLS)
    scr.feed("halfway there")
    scr.feed(render.settle(COLS))
    assert scr.x == 0


def test_settle_is_a_pure_string():
    """No terminal, no I/O: the decision to write it belongs to the caller."""
    assert render.settle(80) == " " * 79 + "\r\x1b[K"
    assert render.settle(1) == " " + "\r\x1b[K"        # degenerate width, still valid
