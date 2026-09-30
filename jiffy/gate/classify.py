"""`classify(line) -> GateVerdict` and the raise-only judge rule.

Classification is pattern matching on code. No judge, no network, no process,
no filesystem write happens in this module. The only inputs are the line, the
built-in tables and the user denylist file.

Normalisation is adversarial-minded but deliberately small: jiffy's own trailing
`# request` comment is removed, whitespace collapses, quoting and `$()`/backtick
nesting are flattened into extra *variants*, and every variant is matched. A
line is classified at the HIGHEST tier any variant reaches — flattening can only
find more, never less. Where normalisation is uncertain (a substitution, an
`eval`, a decoder feeding a shell, unbalanced quotes) the verdict is rounded UP
one tier, capped below DENY so a guess never blocks a line outright.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..types import GateVerdict, Tier
from . import rules as _rules
from .denylist import deny_rules

__all__ = ["classify", "raise_tier", "normalise", "variants", "CLEAN_VERDICT",
           "JUDGE_RULE_ID", "RAISE_CONFIRM_P", "RAISE_NOTE_P"]

CLEAN_VERDICT = GateVerdict(tier=Tier.CLEAN, reason="", rule_id="")

JUDGE_RULE_ID = "judge:destructive"
RAISE_CONFIRM_P = 0.85          # judge is confident this is destructive
RAISE_NOTE_P = 0.60             # judge is uneasy

_QUOTES = "\"'"
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_UNCERTAIN = re.compile(
    r"\$\(|`|\$\{|\beval\b|\bbase64\b|\bxxd\b|\bbase32\b|\\x[0-9a-f]{2}|"
    r"\bprintf\b[^;&|]*\|",
    re.IGNORECASE,
)


def _strip_trailing_comment(line: str) -> str:
    """Drop a trailing `# ...` comment — the form jiffy itself appends.

    Quote-aware, so `echo '# not a comment'` keeps its text. A `#` that is not
    preceded by whitespace (`foo#bar`, `$#`) is not a comment.
    """
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def normalise(line: str) -> str:
    """The canonical form: comment stripped, whitespace collapsed, trimmed."""
    return re.sub(r"\s+", " ", _strip_trailing_comment(line)).strip()


def _flatten_substitutions(text: str) -> str:
    """Inline `$(cmd arg)` / `` `cmd arg` `` as its arguments.

    `rm -rf $(echo /)` becomes `rm -rf /` so the root rule sees the real target.
    Bounded iteration: nesting deeper than 8 stops, and the uncertainty bump
    covers whatever is left.
    """
    for _ in range(8):
        def repl(m: re.Match[str]) -> str:
            inner = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            parts = inner.split()
            return " ".join(parts[1:]) if len(parts) > 1 else inner
        new = _SUBST.sub(repl, text)
        if new == text:
            return new
        text = new
    return text


_IFS = re.compile(r"\$\{IFS[^}]*\}|\$IFS\b")
_ASSIGN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|]+)")
_EVASION = re.compile(
    r"\$\{IFS|\$IFS\b|\beval\b|\bbase64\b|\bbase32\b|\bxxd\b|\\x[0-9a-f]{2}",
    re.IGNORECASE,
)


def _expand_ifs(text: str) -> str:
    """`rm${IFS}-rf${IFS}/` -> `rm -rf /`. IFS defaults to whitespace, so any
    ${IFS...} form is a word separator to the shell and must be one to us."""
    return re.sub(r"\s+", " ", _IFS.sub(" ", text)).strip()


def _expand_assignments(text: str) -> str:
    """`X=/; rm -rf $X` -> `X=/; rm -rf /`. Only literal assignments made
    earlier in the same line; bounded, and anything unresolved stays as-is."""
    env = {m.group(1): m.group(2) for m in _ASSIGN.finditer(text)}
    if not env:
        return text
    out = text
    for _ in range(4):
        prev = out
        for name, val in env.items():
            out = re.sub(r"\$\{%s\}|\$%s\b" % (re.escape(name), re.escape(name)),
                         val.replace("\\", "\\\\"), out)
        if out == prev:
            break
    return out


def variants(line: str) -> tuple[str, ...]:
    """Every form of the line that gets matched. More forms can only find more."""
    base = normalise(line)
    forms = [base]
    unquoted = re.sub(r"\s+", " ", base.translate({ord(q): None for q in _QUOTES})).strip()
    forms.append(unquoted)
    for form in list(forms):
        forms.append(form.replace("\\", ""))
        flat = _flatten_substitutions(form)
        forms.append(flat)
        forms.append(re.sub(r"\s+", " ", flat.translate({ord(q): None for q in _QUOTES})).strip())
        # shell-metacharacter evasions: word-splitting via IFS, and literal
        # variable assignments used later in the same line.
        for expanded in (_expand_ifs(flat), _expand_assignments(flat)):
            forms.append(expanded)
            forms.append(_expand_ifs(_expand_assignments(expanded)))
            forms.append(_flatten_substitutions(expanded))
    seen: dict[str, None] = {}
    for f in forms:
        if f:
            seen.setdefault(f, None)
    return tuple(seen)


def _uncertain(line: str) -> bool:
    forms = variants(line)
    if any(_UNCERTAIN.search(f) for f in forms):
        return True
    base = forms[0] if forms else ""
    return base.count('"') % 2 == 1 or base.count("'") % 2 == 1


def classify(line: str, denylist_path: Path | None = None) -> GateVerdict:
    """Pattern-match `line` to a tier. Pure: same string, same verdict, always.

    DENY is checked first and returns immediately, so nothing downstream in this
    function can soften it. The user denylist is appended to the built-in DENY
    table by `deny_rules()`; there is no path here that shortens it.
    """
    forms = variants(line)
    if not forms:
        return CLEAN_VERDICT

    hit = _rules.first_match(forms, deny_rules(denylist_path))
    if hit is not None:
        return GateVerdict(tier=Tier.DENY, reason=hit.reason, rule_id=hit.rule_id)

    for tier in (Tier.CONFIRM, Tier.NOTE):
        hit = _rules.first_match(forms, _rules.rules_for(tier))
        if hit is not None:
            verdict = GateVerdict(tier=tier, reason=hit.reason, rule_id=hit.rule_id)
            break
    else:
        verdict = CLEAN_VERDICT

    if _uncertain(line):
        return _round_up(verdict, line)
    return verdict


def _round_up(verdict: GateVerdict, line: str = "") -> GateVerdict:
    """One tier up, capped below DENY. Used when normalisation is uncertain."""
    if verdict.tier >= Tier.CONFIRM:
        return verdict
    step = 2 if (line and _EVASION.search(line)) else 1
    tier = Tier(min(int(verdict.tier) + step, int(Tier.CONFIRM)))
    reason = (
        f"{verdict.reason}; could not fully resolve quoting or substitution"
        if verdict.reason
        else "could not fully resolve quoting or substitution — rounded up"
    )
    rule_id = verdict.rule_id or "builtin:unresolved-normalisation"
    return GateVerdict(tier=tier, reason=reason, rule_id=rule_id)


def raise_tier(verdict: GateVerdict, judge_p: float) -> GateVerdict:
    """Apply a judge's destructive probability. RAISE ONLY.

    Structurally, not by intention:

    * the judge's opinion becomes `proposed`, a tier computed from thresholds
      and then clamped with `min(proposed, Tier.CONFIRM)` — DENY is not in the
      reachable range of this function's output beyond what already arrived;
    * the result is `Tier(max(verdict.tier, proposed))` — `Tier` is an ordered
      IntEnum, so the output is never below the input for ANY `judge_p`,
      including NaN, negatives, infinities and garbage;
    * there is no other writer. `GateVerdict` is frozen, so the incoming verdict
      cannot be edited in place by this or any other caller.
    """
    try:
        p = float(judge_p)
    except (TypeError, ValueError):
        p = 0.0
    if p != p:                      # NaN: the judge failed, not the line
        p = 0.0

    if p >= RAISE_CONFIRM_P:
        proposed = Tier.CONFIRM
    elif p >= RAISE_NOTE_P:
        proposed = Tier.NOTE
    else:
        proposed = Tier.CLEAN
    proposed = Tier(min(int(proposed), int(Tier.CONFIRM)))    # never DENY

    tier = Tier(max(int(verdict.tier), int(proposed)))
    if tier == verdict.tier:
        return verdict
    return GateVerdict(
        tier=tier,
        reason=f"the judge reads this as destructive (p={p:.2f})",
        rule_id=JUDGE_RULE_ID,
    )
