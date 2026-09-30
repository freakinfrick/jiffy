"""The four-tier deterministic safety gate. Terminal node, never skippable.

    tier 3  DENY     never reaches the shell. No key, flag or config overrides it.
    tier 2  CONFIRM  red banner naming the reason; a second ENTER runs it.
    tier 1  NOTE     yellow tag; runs normally.
    tier 0  CLEAN    runs.

Public surface:

    evaluate(line, judge_p=None) -> GateVerdict     the gate node
    classify(line)               -> GateVerdict     pattern match only
    raise_tier(verdict, p)       -> GateVerdict     judge may raise, never lower
    may_run(verdict, confirmed)  -> bool            a predicate, not an action

This package contains no way to run anything. It imports no `subprocess`, `os`,
`pty`, `eval` or `exec`; it returns verdicts, and the caller — which owns the
shell — is the only thing that can execute. A DENY line therefore cannot be
executed *by the gate* under any argument, and `may_run` returns False for it
regardless of `confirmed`, so a caller that asks the gate gets one answer.
"""
from __future__ import annotations

from pathlib import Path

from ..types import GateVerdict, Tier
from .classify import (
    CLEAN_VERDICT,
    JUDGE_RULE_ID,
    classify,
    normalise,
    raise_tier,
    variants,
)
from .denylist import DenylistWarning, deny_rules, load_user_rules, parse_denylist
from .rules import CONFIRM_RULES, DENY_RULES, NOTE_RULES, Rule

__all__ = [
    "evaluate", "classify", "raise_tier", "may_run", "banner",
    "normalise", "variants", "CLEAN_VERDICT", "JUDGE_RULE_ID",
    "Rule", "DENY_RULES", "CONFIRM_RULES", "NOTE_RULES",
    "deny_rules", "load_user_rules", "parse_denylist", "DenylistWarning",
    "GateVerdict", "Tier",
]


def evaluate(
    line: str,
    judge_p: float | None = None,
    denylist_path: Path | None = None,
) -> GateVerdict:
    """Classify `line`, then let a judge raise the tier if it wants to.

    `judge_p` is the destructive-Noul probability, or None when no judge ran (a
    timeout, a missing backend). None and a failed judge are
    the same thing here: the pattern verdict stands. A model failure must not
    become a shell failure — and cannot become one, because the judge's only
    influence runs through `raise_tier`.
    """
    verdict = classify(line, denylist_path=denylist_path)
    if judge_p is None:
        return verdict
    return raise_tier(verdict, judge_p)


def may_run(verdict: GateVerdict, confirmed: bool = False) -> bool:
    """Is the frontend allowed to hand this line to the shell?

    DENY: False, and `confirmed` is not consulted — it is not a parameter of
    that branch at all. CONFIRM: only on an explicit second ENTER.
    """
    if verdict.tier >= Tier.DENY:
        return False
    if verdict.tier == Tier.CONFIRM:
        return bool(confirmed)
    return True


def banner(verdict: GateVerdict) -> str:
    """One line for the frontend to show. Names the reason and the rule."""
    if verdict.tier >= Tier.DENY:
        return f"DENIED — {verdict.reason}  [{verdict.rule_id}]"
    if verdict.tier == Tier.CONFIRM:
        return f"CONFIRM — {verdict.reason}. ENTER again to run.  [{verdict.rule_id}]"
    if verdict.tier == Tier.NOTE:
        return f"note: {verdict.reason}"
    return ""
