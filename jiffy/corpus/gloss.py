"""What each flag in a line does, in its own page's words.

jiffy should teach, not only autofill: `tree -L 2 ~` is more useful when the
prompt also says what `-L 2` means. The option tables (`options.json`) already
carry every flag's description, so this is code only — no judge, no man call,
nothing run. A flag the table does not know is left unexplained, never guessed.
"""
from __future__ import annotations

import re
import shlex
from typing import Mapping

from jiffy.corpus.options import Option, Spec

__all__ = ["gloss", "first_sentence"]

_OPS = {"|", "||", "&&", ";", "&", "|&"}
_REDIRECT = {">", ">>", "<", "<<", "<<<"}
_PREFIX = {"sudo", "env", "time", "nice", "nohup", "command", "exec"}


_SENTENCE_END = re.compile(r"(?<=\w)\.\s+(?=[A-Z(])")


def first_sentence(desc: str) -> str:
    """Up to the first full stop that ends a sentence. `starting with .` keeps its dot."""
    text = " ".join(desc.split())
    text = _SENTENCE_END.split(text, 1)[0]
    return text[:-1] if text.endswith(".") and text[-2:-1].isalnum() else text


def _tokens(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:                  # an unclosed quote while the user types
        return line.split()


def _segments(toks: list[str]) -> list[list[str]]:
    """`a | b && c` -> [[a], [b], [c]], redirections and their targets dropped."""
    out, cur, skip = [], [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if t in _OPS:
            out.append(cur)
            cur = []
        elif t in _REDIRECT or (t[:-1].isdigit() and t[-1:] in "<>"):
            skip = True
        else:
            cur.append(t)
    out.append(cur)
    return [s for s in out if s]


def _command(seg: list[str], specs: Mapping[str, Spec]) -> tuple[Spec | None, list[str]]:
    """The spec a segment runs and the words after its name. `git log` beats `git`."""
    i = 0
    while i < len(seg) and (seg[i] in _PREFIX or ("=" in seg[i] and not seg[i].startswith("-"))):
        i += 1
    if i >= len(seg):
        return None, []
    two = " ".join(seg[i:i + 2])
    if two in specs:
        return specs[two], seg[i + 2:]
    return specs.get(seg[i]), seg[i + 1:]


def _takes_next(o: Option, tok: str) -> bool:
    """`-n 3` and `--max-count 3` both take the next word: getopt allows a space even
    where the page spells `=`. Only a glued (`-L<n>`) or optional argument does not."""
    return bool(o.arg) and not o.optional and o.joiner != "" and "=" not in tok


def gloss(line: str, specs: Mapping[str, Spec], limit: int = 6) -> list[tuple[str, str]]:
    """Every documented flag in `line` -> (the flag as written, what it does). In order, once each."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(shown: str, o: Option) -> None:
        if o.flags not in seen and o.desc:
            seen.add(o.flags)
            out.append((shown, first_sentence(o.desc)))

    for seg in _segments(_tokens(line)):
        spec, rest = _command(seg, specs)
        if spec is None:
            continue
        i = 0
        while i < len(rest):
            tok = rest[i]
            i += 1
            if tok == "--":
                break
            if not tok.startswith("-") or tok == "-":
                continue
            o = spec.option(tok.split("=", 1)[0])
            if o is not None:
                if _takes_next(o, tok) and i < len(rest):
                    add(f"{tok} {rest[i]}", o)
                    i += 1
                else:
                    add(tok, o)
                continue
            if tok.startswith("--") or len(tok) < 3:
                continue
            o = spec.option(tok[:2])
            if o is not None and o.arg:                     # `-L2`, `-n20`: the value is glued on
                add(tok, o)
                continue
            parts = [spec.option(f"-{c}") for c in tok[1:]]
            if all(parts):                                  # `-la` is `-l` and `-a`
                for c, p in zip(tok[1:], parts):
                    add(f"-{c}", p)
    return out[:limit]
