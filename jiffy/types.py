"""Shared vocabulary. Every unit imports these; no unit re-declares them.

All dataclasses are frozen: a value that crossed a node boundary is a record of
what happened, not a scratchpad. Nodes produce new objects (`dataclasses.replace`)
rather than mutating what they were handed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

__all__ = [
    "Example", "Doc", "Candidate", "Assembled", "Fill",
    "Tier", "GateVerdict", "NodeTrace", "Trace", "Answer",
]


@dataclass(frozen=True)
class Example:
    """One documented invocation of a command."""

    does: str   # what this invocation is for, prose
    line: str   # the command line, may carry {{placeholder}} tokens


@dataclass(frozen=True)
class Doc:
    """One documented command page, from any of the three sources."""

    name: str
    plat: str                      # tldr platform dir: "common", "linux", ...
    desc: str
    examples: tuple[Example, ...] | list[Example]
    source: str                    # "tldr" | "recipe" | "help" | "man"


@dataclass(frozen=True)
class Candidate:
    """A retrieved command under consideration, with whatever the pipeline scored it.

    `scores` is per-stage judge output keyed by stage name, so a trace can show what
    each stage thought without the stages agreeing on a single scalar.
    """

    name: str
    doc: Doc
    scores: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    """One `{{token}}` and what became of it.

    `value` empty means it stayed a token: nothing in the request named it, and
    the line still cannot run. `source` says who offered the value, which is how
    the prompt colours a value the user typed differently from one inferred here.
    """

    token: str
    value: str                    # "" when unresolved
    source: str                   # query | cwd | env | history | spelling | ""
    why: str = ""                 # for --explain; never shown in the line
    start: int = -1               # where it landed in the filled line, for colouring
    end: int = -1


@dataclass(frozen=True)
class Assembled:
    """The command string CODE built. The judge picks; it never writes this."""

    line: str            # the bare command line
    request: str         # the user's words, verbatim
    comment_line: str    # final shipped form: `line  # request`
    placeholders: list[str] = field(default_factory=list)   # {{...}} tokens, in order
    fills: tuple[Fill, ...] = ()                            # what fill did, token by token


class Tier(IntEnum):
    """Gate tiers. Ordered: a higher value is strictly more restrictive."""

    CLEAN = 0
    NOTE = 1
    CONFIRM = 2
    DENY = 3


@dataclass(frozen=True)
class GateVerdict:
    tier: Tier
    reason: str      # human-facing, names why — shown in the banner
    rule_id: str     # which rule fired, e.g. "builtin:rm-rf-root", "judge:destructive"


@dataclass(frozen=True)
class NodeTrace:
    """What one pipeline stage did."""

    node_id: str       # the stage name
    kind: str          # retrieve | pick | invoke | critic | compose | fill | gate
    judge: str         # backend that actually answered, or "" for code-only nodes
    in_n: int
    out_n: int
    ms: float
    usd: float
    degraded: bool     # timed out or errored and fell through to its fallback
    detail: str        # free-form: probabilities, margin, fallback taken, error text


@dataclass(frozen=True)
class Trace:
    """One full run. `--explain` prints this."""

    query: str
    nodes: list[NodeTrace] = field(default_factory=list)
    total_ms: float = 0.0
    total_usd: float = 0.0

    def render(self) -> str:
        """Human-readable trace for `jiffy --explain`."""
        head = f"{self.query!r}"
        rows = [
            f"  {n.node_id:<12} {n.kind:<9} {n.judge or '-':<8} "
            f"{n.in_n:>4}->{n.out_n:<4} {n.ms:>7.0f}ms ${n.usd:.4f}"
            f"{'  DEGRADED' if n.degraded else ''}"
            + (f"  {n.detail}" if n.detail else "")
            for n in self.nodes
        ]
        tail = f"  total {self.total_ms:.0f}ms ${self.total_usd:.4f}"
        return "\n".join([head, *rows, tail])


@dataclass(frozen=True)
class Answer:
    """What one turn produced. The frontend renders exactly this."""

    assembled: Assembled
    candidate: Candidate                 # the command that won
    alternates: list[Candidate]          # ^N/^P cycle order, best first
    verdict: GateVerdict
    trace: Trace
