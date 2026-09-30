"""Build a line from a command's option table, for when no documented line fits.

tldr documents 8 lines per command; the request often needs a line that is not
one of them (`ls ~/*.py`, `git log --oneline -n 20`). This module is the code
half of the `compose` stage: everything here is deterministic, and the judge's
only part (in `pipeline.compose`) is a yes/no per option on a short list built here.

Who writes what, as everywhere in jiffy:
  * flags come from the man page (`jiffy.corpus.options`), never invented;
  * a flag's argument becomes a `{{token}}` the fill stage resolves from the request,
    except one whose kind is unknown: that blank is CLOSED to the values the page
    lists for it (`closed`), since a request word is not a valid `-d TYPE`;
  * so does a positional (`FILE`, `starting-point`): an optional one is written
    `{{?token}}`, which fill drops when the request states nothing for it.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

from jiffy.corpus.options import Option, Positional, Spec
from jiffy.fill.sources import from_query
from jiffy.fill.tokens import Kind, classify

__all__ = ["relevant", "spell", "arg_token", "closed", "prune", "with_leaders", "positional_parts", "build_line", "describe"]

_STOP = set("a an the to of in on at for from by with and or my me i it is are be this that these those all "
            "each every any some show list print give get make do does how what which who where want please "
            "file files one only just".split())
#: options no request ever wants composed into a line
_NEVER = {"--help", "--version", "-?", "--usage"}
_NEVER_DESC = ("display this help", "output version information", "print version", "show version",
               "display version", "print help", "show help")

_DIR_NAMES = ("dir", "directory", "directories", "starting-point", "folder")
_FILE_NAMES = ("file", "files", "path", "paths", "name", "target")


def _words(text: str) -> list[str]:
    """Lower-cased content words, crudely stemmed: `files`/`filed` -> `file`, `sorted` -> `sort`.

    Adjacent pairs are also joined (`one line` -> `oneline`), since flags glue words.
    """
    raw = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in raw + [a + b for a, b in zip(raw, raw[1:])]:
        if w in _STOP or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 2 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


def usable(o: Option) -> bool:
    return not (set(o.flags) & _NEVER) and not any(d in o.desc.lower() for d in _NEVER_DESC)


def relevant(spec: Spec, query: str, k: int = 16) -> list[Option]:
    """The options whose flag or description shares words with the request, best first.

    IDF-weighted over this command's own options, so a word every option mentions
    (`list` in `ls`) counts for little and a rare one (`human`, `recursive`) for a lot.
    Options sharing nothing are never asked about: the judge only sees a short list.
    """
    opts = [o for o in spec.options if usable(o)]
    if not opts:
        return []
    docs = [set(_words(" ".join(o.flags).replace("-", " ") + " " + o.desc)) for o in opts]
    df = Counter(w for d in docs for w in d)
    q = set(_words(query))
    scored = []
    for i, (o, d) in enumerate(zip(opts, docs)):
        s = sum(math.log(1 + len(opts) / df[w]) for w in q & d)
        if s > 0:
            scored.append((-s, i, o))
    return [o for _, _, o in sorted(scored)[:k]]


def arg_token(o: Option) -> str:
    """The `{{...}}` text for an option's argument, named so `fill.tokens.classify` reads it right."""
    raw = o.arg.strip()
    low = raw.lower().strip("<>")
    if "size" in " ".join(o.flags).lower() or "size" in low or "[cwbkmg]" in low:
        return "size"
    low = re.sub(r"\[.*?\]", "", low).strip("<>= ")
    if low in ("n", "num", "number", "count", "levels", "level", "lines", "k", "depth", "max"):
        return "n"
    if low == "list" and re.search(r"\b(fields?|bytes?|characters?|columns?)\b", o.desc.lower()):
        return "n"                          # `cut -f LIST`: "select only these fields"
    if low in ("dir", "directory"):
        return "path/to/directory"
    if low in ("file", "filename", "path"):
        return "path/to/file"
    if len(low) <= 2:
        # `-type c`: a one-letter name says nothing, and `c` or `h` would read as a
        # file extension and be filled with one. Name the blank after its flag.
        return max(o.flags, key=len).lstrip("-") or "value"
    return low or "value"


def _stated(o: Option, query: str) -> bool:
    """Did the request type a value this option's argument could take?"""
    return any(c.strong for c in from_query(classify(arg_token(o)), query))


def spell(o: Option, value: str | None = None, query: str = "") -> str:
    """`-n 20`, `--max-count=20`, `-maxdepth 1`: the short spelling, the argument attached the documented way.

    An optional argument (`--color[=WHEN]`) is left off, unless the request states a
    value for it: then the long spelling carries it, since `-n 20` would not attach.
    """
    flag = o.short
    if o.optional and o.arg and query and _stated(o, query):
        longest = max(o.flags, key=len)
        return f"{longest}={{{{{arg_token(o)}}}}}" if longest.startswith("--") else f"{longest}{{{{{arg_token(o)}}}}}"
    if not o.arg or o.optional:
        return flag
    v = value if value is not None else "{{" + arg_token(o) + "}}"
    if flag.startswith("--"):
        return f"{flag}{o.joiner}{v}"
    return f"{flag}{'' if o.joiner == '' else ' '}{v}"


def closed(chosen: Sequence[Option]) -> dict[str, tuple[str, ...]]:
    """Blank -> the only values fill may offer it, for each flag argument of unknown kind.

    A typed blank (a number, a file, a pattern) is the request's to answer. An
    untyped one (`TYPE`, `WHEN`, `query-gpu`) takes values the command defines, so
    fill offers the page's list, or nothing when the page lists none.
    """
    out: dict[str, tuple[str, ...]] = {}
    for o in chosen:
        if o.arg and classify(arg_token(o)) is Kind.UNKNOWN:
            out[arg_token(o)] = out.get(arg_token(o), ()) + o.values
    return out


def prune(chosen: Sequence[Option]) -> list[Option]:
    """Drop options that repeat another chosen one, best first in, best kept.

    Two shapes, both read off the page: an option whose description names another
    chosen flag as one of its forms (`--sort=WORD ... size (-S)` beside `-S`), and a
    second option taking the same blank (`-resize`, `-scale`, `-extent` each want a
    `{{geometry}}`; one says it).
    """
    flags = {f for o in chosen for f in o.flags}
    cites = [o for o in chosen if o.arg and any(f"({f})" in o.desc for f in flags - set(o.flags))]
    out: list[Option] = []
    blanks: set[str] = set()
    for o in chosen:
        if o in cites or (o.arg and arg_token(o) in blanks):
            continue
        if o.arg:
            blanks.add(arg_token(o))
        out.append(o)
    return out


def with_leaders(spec: Spec, chosen: Sequence[Option]) -> list[Option]:
    """The chosen options plus each one's mode flag, the leader first: `-d` -> `-q -d`.

    The page says which flags only work under another (`Option.needs`); a line with
    `-d TEMPERATURE` and no `-q` is an error, not a narrower answer.
    """
    out: list[Option] = []
    for o in chosen:
        lead = spec.option(o.needs) if o.needs else None
        if lead is not None and lead not in out and lead not in chosen:
            out.append(lead)
        out.append(o)
    return out


def _kind(p: Positional) -> str:
    n = p.name.lower()
    if any(k in n for k in _DIR_NAMES):
        return "dir"
    if any(k == n or n.startswith(k) or n.endswith(k) for k in _FILE_NAMES):
        return "file"
    if "pattern" in n or n in ("regex", "string", "text", "word"):
        return "pattern"
    if n in ("expression",):
        return "expression"
    return "other"


#: the token each kind of positional becomes; fill resolves it
_TOKEN = {"dir": "path/to/directory", "file": "path/to/file", "pattern": "pattern"}


def positional_parts(spec: Spec) -> list[tuple[str, str]]:
    """Each positional -> (kind, text): a `{{token}}`, `{{?token}}` when optional, or "" (omit)."""
    out = []
    for p in spec.positionals:
        k = _kind(p)
        name = _TOKEN.get(k) or (p.name if k != "expression" else "")
        text = "" if not name or (p.optional and k not in _TOKEN) else "{{" + "?" * p.optional + name + "}}"
        out.append((k, text))
    return out


def build_line(name: str, spec: Spec, chosen: Sequence[Option], query: str) -> str:
    """`name [options] [positionals]` — or, when the synopsis has an `expression`
    (find), the positionals before it and the options in its place."""
    parts = positional_parts(spec)
    flags = [spell(o, query=query) for o in chosen]
    words = [name]
    if any(k == "expression" for k, _ in parts):
        for k, text in parts:
            if k == "expression":
                words += flags
                flags = []
            elif text:
                words.append(text)
        words += flags
    else:
        words += flags + [text for _, text in parts if text]
    return " ".join(words)


def describe(desc: str, chosen: Sequence[Option]) -> str:
    """What the composed line does, from the page's own words: the command, then each flag."""
    bits = [desc.strip().rstrip(".")] if desc else []
    bits += [f"{o.short}: {o.desc.split('. ')[0].rstrip('.')}" for o in chosen if o.desc]
    return "; ".join(bits)
