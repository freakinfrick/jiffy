"""User denylist: `~/.config/jiffy/denylist.txt`, merged into the DENY set.

One regex or literal per line; `#` starts a comment; blank lines ignored.

Two properties this module exists to guarantee:

1. **Additive only.** `load_user_rules()` returns rules, and `deny_rules()`
   returns `builtin + user`. There is no syntax for removing a built-in, no
   "allow" prefix, no negation, and no argument anywhere in this module that
   makes the built-in table shorter. A user file that is empty, missing,
   unreadable or garbage yields the built-in set unchanged.

2. **Loud, never fatal, never truncating.** A line that will not compile as a
   regex is reported through `warnings.warn` and re-read as a literal (the
   round-up choice: an unparseable pattern still denies its own text rather
   than denying nothing), and parsing continues to the end of the file.
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

from ..types import Tier
from .rules import DENY_RULES, Rule

__all__ = ["DenylistWarning", "parse_denylist", "load_user_rules", "deny_rules"]


class DenylistWarning(UserWarning):
    """A user denylist line that could not be read as written."""


def _default_path() -> Path:
    from .. import config as _config      # local import: config owns the path
    return _config.DENYLIST_PATH



# A user regex runs on every keystroke (the gate backs the prompt validator and
# the toolbar), so a catastrophically backtracking pattern freezes the UI. We
# refuse to compile the classic nested-quantifier shapes and fall back to a
# literal match, which is always safe and never slow.
_REDOS = re.compile(
    r"\((?:[^()]*[+*][^()]*)\)\s*[+*]"      # (a+)+ , (a*)* , (x|y+)*
    r"|\((?:[^()]*\{\d+,\}?[^()]*)\)\s*[+*{]"  # (a{2,})+
    r"|\[[^\]]*\]\s*[+*]\s*\[[^\]]*\]\s*[+*]",  # [a]+[b]+ adjacency
)
_MAX_PATTERN = 512


def _redos_risk(pattern: str) -> str:
    """Return a reason string if `pattern` looks catastrophic, else ''."""
    if len(pattern) > _MAX_PATTERN:
        return f"longer than {_MAX_PATTERN} characters"
    if _REDOS.search(pattern):
        return "nested quantifier that can backtrack catastrophically"
    return ""


def parse_denylist(text: str, origin: str = "denylist.txt") -> list[Rule]:
    """Parse denylist text into rules. Never raises on bad input."""
    out: list[Rule] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        rule_id = f"user:{origin}:{lineno}"
        reason = f"matches your denylist ({origin} line {lineno}): {line}"
        try:
            risk = _redos_risk(line)
            if risk:
                warnings.warn(
                    f"{origin}: pattern {line!r} rejected as a regex ({risk}); "
                    "matching it literally instead. The rule still DENIES, it "
                    "is simply not treated as a pattern.",
                    stacklevel=2,
                )
                raise re.error(risk)
            pattern = re.compile(line, re.IGNORECASE)
        except re.error as exc:
            warnings.warn(
                f"{origin} line {lineno}: not a valid regex ({exc}); "
                f"treating it as a literal string. Rest of the file still loaded.",
                DenylistWarning,
                stacklevel=2,
            )
            pattern = re.compile(re.escape(line), re.IGNORECASE)
        out.append(Rule(rule_id, pattern, reason))
    return out


def load_user_rules(path: Path | None = None) -> list[Rule]:
    """Rules from the user's denylist file. Missing or unreadable file => []."""
    p = Path(path) if path is not None else _default_path()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except OSError as exc:
        warnings.warn(
            f"could not read {p}: {exc}; built-in DENY rules still apply.",
            DenylistWarning,
            stacklevel=2,
        )
        return []
    return parse_denylist(text, origin=p.name)


def deny_rules(path: Path | None = None) -> tuple[Rule, ...]:
    """The full DENY set: built-ins first, then user additions.

    Built-ins are listed first so a built-in reason wins when both match, and
    because nothing downstream can shorten the head of this tuple.
    """
    assert Tier.DENY == 3          # the tier this table feeds, spelled out
    return (*DENY_RULES, *load_user_rules(path))
