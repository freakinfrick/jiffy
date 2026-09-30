"""The only thing in jiffy that can run a command.

Every execution path in the frontend funnels through `execute()`, and
`execute()` asks `jiffy.gate.may_run` first. A DENY verdict returns
`Refusal` — `confirmed` is not consulted, there is no flag, no env var and no
key that reaches the runner with one. `jiffy/tui` imports `subprocess` here and
nowhere else, so the chokepoint is greppable, not merely intended.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable

from jiffy import gate
from jiffy.types import GateVerdict, Tier

from .placeholders import has_placeholder

__all__ = ["execute", "Refusal", "Result", "user_shell"]


@dataclass(frozen=True)
class Refusal:
    reason: str
    verdict: GateVerdict | None = None


@dataclass(frozen=True)
class Result:
    line: str
    returncode: int


def user_shell() -> str:
    return os.environ.get("SHELL") or "/bin/sh"


def _spawn(line: str) -> int:
    """`$SHELL -c line`, stdio inherited so output streams and the exit code survives.

    Ctrl-C while the command runs belongs to the command, as in any shell. The
    terminal sends SIGINT to the whole foreground group, so jiffy holds a no-op
    handler until the child exits; before, the wait raised KeyboardInterrupt and
    took the TUI down. A no-op handler, not SIG_IGN: exec resets handlers but
    inherits ignores, and the child must still die on Ctrl-C.
    """
    try:
        old = signal.signal(signal.SIGINT, lambda *_: None)
    except ValueError:                        # not the main thread: nothing to guard
        old = None
    try:
        code = subprocess.call([user_shell(), "-c", line])
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)
    return 128 - code if code < 0 else code   # killed by signal N reads as 128+N, like bash


def execute(line: str, verdict: GateVerdict, confirmed: bool = False,
            runner: Callable[[str], int] = _spawn) -> Result | Refusal:
    """Run `line` iff the gate allows it. Returns the exit code or a refusal."""
    if not line.strip():
        return Refusal("nothing to run")
    if "\n" in line or "\r" in line:
        return Refusal("refusing a multi-line command")
    if has_placeholder(line):
        return Refusal("fill the {{placeholders}} first")
    if verdict.tier >= Tier.DENY or not gate.may_run(verdict, confirmed):
        if verdict.tier >= Tier.DENY:
            return Refusal(gate.banner(verdict), verdict)
        return Refusal("confirm required", verdict)
    return Result(line, int(runner(line)))


def preview(line: str) -> str:
    """How the line would be handed to the shell. Used by `--explain` output."""
    return " ".join(shlex.quote(p) for p in [user_shell(), "-c", line])
