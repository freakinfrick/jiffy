"""Where a value may come from. Every source only ever OFFERS.

    query   spans the user typed: quoted strings, paths, file names, globs,
            numbers, sizes, hosts, URLs, and their plain words.
    cwd     entries really here, when the request names one.
    env     $USER, the hostname, the users, directories and sensor chips this box
            really has, and the four places every shell knows: `.`, `~`, `..`, `/`.
    history paths and hosts this user has used before (weak: one shared word is a hint).
    mime    file extensions, from `/etc/mime.types` (read at runtime).

Nothing here knows what "home directory" or "python files" means. A proposal is
either a span the user typed verbatim (`strong`) or something a judge must match
to the request (`weak`: the places, the words, the globs built from them, the
extensions of a type the request names). With no judge only strong proposals
fill; a weak one needs a pick. A source with no evidence returns nothing, and
the token stays unfilled rather than becoming a plausible guess:
`rm -rf {{path/to/directory}}` must never quietly become `rm -rf ~`.
"""
from __future__ import annotations

import os
import re
import shlex
import socket
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from .tokens import Kind

__all__ = ["Candidate", "Sources", "from_query", "from_cwd", "from_env", "from_history",
           "mime_extensions", "MIME_TYPES"]

MIME_TYPES = Path("/etc/mime.types")

_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")
_PATHISH = re.compile(r"(?:^|\s)(~[\w./-]*|\.{1,2}/[\w./-]+|/[\w./-]+|[\w.-]+/[\w./-]*)")
_CALLED = re.compile(r"\b(?:called|named)\s+([\w][\w.-]*)\b")
_GLOB = re.compile(r"(?:^|\s)(\*\.[A-Za-z0-9]+|[\w.-]*\*[\w.-]*)")
_DOTTED = re.compile(r"(?<![\w./~-])([\w-]+(?:\.[\w-]+)+)(?![\w.])")
_NUMBER = re.compile(r"\b(\d+)\b")
_WORD = re.compile(r"[\w.+-]+")
_HOSTISH = re.compile(r"\b([\w-]+\.[\w.-]+\b|\d{1,3}(?:\.\d{1,3}){3})\b")
_URL = re.compile(r"\b((?:https?://|www\.)[^\s'\"]+)")

#: a number and a unit, as people write them -> the suffix find/split/du write
_SIZE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(k|kb|kib|kilobytes?|m|mb|mib|megs?|megabytes?|"
                   r"g|gb|gib|gigs?|gigabytes?|t|tb|tib|terabytes?)\b")
_UNIT = {"k": "k", "m": "M", "g": "G", "t": "T"}

#: English function words: never a value, and offering them only lengthens the list
_STOP = frozenset(
    "a an the to of in on at for from by with and or not no my me i it its is are be this that these those "
    "all each every any some into onto than then there here what which who whom where when how do does "
    "please can could would should will just only one ones".split())

#: the places every shell knows, and what each is
ANCHORS = ((".", "the current directory"), ("~", "your home directory"),
           ("..", "the parent directory"), ("/", "the root of the filesystem"))

PATH_KINDS = (Kind.DIR, Kind.FILE, Kind.PATH)

#: kinds a quoted string can never satisfy: they want a structured value, so
#: `'~/launchpad'` must not fill the slot that expects a number (`-L {{level}}`).
_QUOTE_BLIND = frozenset({Kind.NUMBER, Kind.PORT, Kind.SIZE, Kind.EXT})

#: at most this many plain words become name fragments, and this many options go to one pick
_MAX_FRAGMENTS, MAX_OPTIONS = 12, 120


@dataclass(frozen=True)
class Candidate:
    """One possible value, and where it came from."""

    value: str
    source: str                # query | cwd | env | history
    why: str = ""              # shown in --explain and to the judge, never in the line
    strong: bool = True        # typed verbatim / a fact; False = only a judge may choose it


@dataclass(frozen=True)
class Sources:
    """The world this fill may look at. Every field is optional and inert by default."""

    cwd: Path | None = None
    env: dict[str, str] | None = None
    history: tuple[str, ...] = ()
    mime: Path | None = None
    home: Path | None = None           # its entries are offered when the request names one
    root: Path | None = None           # likewise `/`

    @classmethod
    def live(cls, history: bool = True, limit: int = 400) -> "Sources":
        """The real box. Reads only; writes nothing, ever."""
        return cls(cwd=Path.cwd(), env=dict(os.environ),
                   history=_read_history(limit) if history else (),
                   mime=MIME_TYPES, home=Path.home(), root=Path("/"))


def _read_history(limit: int) -> tuple[str, ...]:
    """The tail of the user's shell history. A missing or unreadable file is fine."""
    for name in (".bash_history", ".zsh_history"):
        path = Path.home() / name
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        if lines:
            return tuple(lines[-limit:])
    return ()


@lru_cache(maxsize=4)
def mime_extensions(path: Path | None) -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    """`/etc/mime.types` -> (word -> extensions, every extension).

    A word is a whole subtype, less any `x-`, or one of the line's own extensions:
    `text/x-python py` files `py` under `python`, `text/plain txt text` files both
    under `text` and under `txt`. An absent
    or unreadable file is no table at all, not an error.
    """
    by_word: dict[str, list[str]] = {}
    every: set[str] = set()
    if path is None:
        return {}, frozenset()
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}, frozenset()
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2 or "/" not in fields[0]:
            continue
        exts = [e.lower() for e in fields[1:] if re.fullmatch(r"[a-z0-9][\w+-]*", e.lower())]
        if not exts:
            continue
        every.update(exts)
        sub = fields[0].split("/", 1)[1].lower()
        bucket = by_word.setdefault(sub[2:] if sub.startswith("x-") else sub, [])
        bucket += [e for e in exts if e not in bucket]
        for ext in exts:                                   # `text` also names txt's line, itself first
            bucket = by_word.setdefault(ext, [ext])
            bucket += [e for e in exts if e not in bucket]
    return {w: tuple(v) for w, v in by_word.items()}, frozenset(every)


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    seen, out = set(), []
    for c in cands:
        if c.value and c.value not in seen:
            seen.add(c.value)
            out.append(c)
    return out


def _words(query: str) -> list[str]:
    """The request's own words, in order, minus function words. `I` survives: a capital is deliberate."""
    out = []
    for w in _WORD.findall(query):
        w = w.rstrip(".,")
        if w and (w == "I" or w.lower() not in _STOP) and w not in out:
            out.append(w)
    return out


def file_types(query: str, mime: Path | None) -> list[str]:
    """Extensions of every type the request names by a word: "python" -> py, pyc, pyo."""
    by_word, _ = mime_extensions(mime)
    out: list[str] = []
    for w in _words(query.lower()):
        typed = (w[1:],) if re.fullmatch(r"\.[a-z0-9]+", w) else ()     # `.tmp`: they typed the extension
        w = w.lstrip(".")
        for ext in typed + by_word.get(w, ()):
            if ext not in out:
                out.append(ext)
    return out


def filenames(query: str, mime: Path | None) -> list[str]:
    """Dotted words ending in a known extension: `deploy.sh`, `notes.txt`. `google.com` is not one."""
    _, every = mime_extensions(mime)
    return [m.group(1) for m in _DOTTED.finditer(query)
            if m.group(1).rsplit(".", 1)[1].lower() in every]


def sizes(query: str) -> list[tuple[str, str]]:
    """(number+unit, as typed) for every size stated. "100 megabytes" -> ("100M", ...)."""
    return [(f"{m.group(1)}{_UNIT[m.group(2)[0]]}", m.group(0)) for m in _SIZE.finditer(query.lower())]


def _globs(query: str, exts: list[str]) -> list[Candidate]:
    """Name patterns built from the request's words and types: `I*`, `*_old`, `*.md`, `I*.md`.

    Code does not read "starting with"; it offers every shape and the judge matches one
    to what was said.
    """
    frags = [w for w in _words(query) if w.lower().lstrip(".") not in exts][:_MAX_FRAGMENTS]
    out = [Candidate(f"*.{e}", "query", f"files of type {e}", False) for e in exts]
    for f in frags:
        out += [Candidate(f"{f}*", "query", f"names starting with {f}", False),
                Candidate(f"*{f}", "query", f"names ending with {f}", False),
                Candidate(f"*{f}*", "query", f"names containing {f}", False)]
        for e in exts:
            out += [Candidate(f"{f}*.{e}", "query", f"{e} files whose names start with {f}", False),
                    Candidate(f"*{f}*.{e}", "query", f"{e} files whose names contain {f}", False)]
    return out


#: counts and ordinals written as words ("the second field"). Offered weak, and
#: only for a word the request contains, so the judge can pick but never invent.
#: Re-admitted 2026-09-28 by user decision (phase 4 had deleted the number words).
NUMBER_WORDS = {w: str(n) for n, ws in (
    (1, ("one", "first")), (2, ("two", "second")), (3, ("three", "third")), (4, ("four", "fourth")),
    (5, ("five", "fifth")), (6, ("six", "sixth")), (7, ("seven", "seventh")), (8, ("eight", "eighth")),
    (9, ("nine", "ninth")), (10, ("ten", "tenth")), (11, ("eleven", "eleventh")), (12, ("twelve", "twelfth")),
    (20, ("twenty", "twentieth")),
) for w in ws}
#: separators named in words, as shell arguments; same rule as NUMBER_WORDS
DELIM_WORDS = {w: v for ws, v in (
    (("comma", "commas"), ","), (("colon", "colons"), ":"), (("semicolon", "semicolons"), "';'"),
    (("tab", "tabs"), "$'\\t'"), (("space", "spaces"), "' '"), (("pipe", "pipes"), "'|'"),
    (("slash", "slashes"), "/"), (("dash", "dashes", "hyphen", "hyphens"), "-"),
    (("dot", "dots", "period", "periods"), "."), (("underscore", "underscores"), "_"),
) for w in ws}
#: a separator typed as itself: "split on :", "separated by ;"
_DELIM_SAID = re.compile(r"\b(?:by|on|with|at)\s+([^\w\s'\"`])(?=\s|$)")


def _lower_words(query: str) -> list[str]:
    return re.findall(r"[a-z]+", query.lower())


def from_query(kind: Kind, query: str, mime: Path | None = None) -> list[Candidate]:
    """What the user said: typed spans first (strong), then words and shapes built from them (weak)."""
    out: list[Candidate] = []

    if kind not in _QUOTE_BLIND:
        for group in _QUOTED.findall(query):           # they quoted it: they meant it
            out.append(Candidate(group, "query", "quoted"))

    if kind in PATH_KINDS:
        for hit in _PATHISH.findall(query):
            out.append(Candidate(hit, "query", "written as a path"))
        for hit in _CALLED.findall(query):
            out.append(Candidate(hit, "query", "the name you gave"))
        if kind is not Kind.DIR:
            for hit in filenames(query, mime):
                out.append(Candidate(hit, "query", "a file you named"))
            out += [Candidate(m.group(1), "query", "a dotted name in the request", False)
                    for m in _DOTTED.finditer(query)]
            for hit in _GLOB.findall(query):
                out.append(Candidate(hit, "query", "written as a glob"))
            exts = file_types(query, mime)
            out += [Candidate(f"*.{e}", "query", f"files of type {e}", False) for e in exts]

    if kind is Kind.PATTERN:
        for hit in _GLOB.findall(query):
            out.append(Candidate(hit, "query", "written as a glob"))
        for hit in filenames(query, mime):
            out.append(Candidate(hit, "query", "a file you named"))
        out += _globs(query, file_types(query, mime))
        out += [Candidate(w, "query", "a word in the request", False) for w in _words(query)]

    if kind is Kind.EXT:
        out += [Candidate(e, "query", "extension of a type you named", False)
                for e in file_types(query, mime)]

    if kind is Kind.SIZE:
        for value, said in sizes(query):
            out.append(Candidate(value, "query", "a size you gave"))
            out += [Candidate(s + value, "query", f"{'more' if s == '+' else 'less'} than {said}", False)
                    for s in "+-"]

    if kind in (Kind.NUMBER, Kind.PORT):
        taken = {m.span(1) for m in _SIZE.finditer(query.lower())}
        for m in _NUMBER.finditer(query):
            if m.span(1) not in taken:
                out.append(Candidate(m.group(1), "query", "a number in the request"))
    if kind is Kind.NUMBER and not out:           # a typed number beats a number word
        out += [Candidate(NUMBER_WORDS[w], "query", f"'{w}' in the request", False) for w in _lower_words(query)
                if w in NUMBER_WORDS]

    if kind is Kind.DELIM:
        for m in _DELIM_SAID.finditer(query):
            out.append(Candidate(shlex.quote(m.group(1)), "query", "the separator you typed"))
        out += [Candidate(DELIM_WORDS[w], "query", f"'{w}' in the request", False) for w in _lower_words(query)
                if w in DELIM_WORDS]

    if kind is Kind.HOST:
        for hit in _HOSTISH.findall(query):
            out.append(Candidate(hit, "query", "looks like a host"))

    if kind is Kind.URL:
        for hit in _URL.findall(query):
            out.append(Candidate(hit, "query", "a URL you gave"))
        out += [Candidate(h, "query", "looks like a host", False) for h in _HOSTISH.findall(query)]

    if kind in (Kind.UNKNOWN, Kind.USER, Kind.HOST, *PATH_KINDS):
        out += [Candidate(w, "query", "a word in the request", False) for w in _words(query)]

    return _dedupe(out)


def _named_entries(base: Path | None, shown: str, query: str, kind: Kind, source: str,
                   why: str) -> list[Candidate]:
    """Entries of `base` whose name is a word of the request. `shown` is how the line spells `base`."""
    if base is None or kind not in PATH_KINDS:
        return []
    words = {w.lower() for w in re.findall(r"[\w.-]+", query) if len(w) > 2}
    if not words:
        return []
    try:
        entries = sorted(p for p in base.iterdir() if not p.name.startswith("."))
    except OSError:
        return []
    out = []
    for p in entries:
        try:
            if kind is Kind.DIR and not p.is_dir() or kind is Kind.FILE and not p.is_file():
                continue
        except OSError:
            continue
        if p.stem.lower() in words or p.name.lower() in words:
            out.append(Candidate(shown + p.name, source, why))
    return out


def from_cwd(kind: Kind, query: str, cwd: Path | None) -> list[Candidate]:
    """What is really here. Only offered when the query names it."""
    return _dedupe(_named_entries(cwd, "", query, kind, "cwd", "a real entry here, named in the request"))


def _users(root: Path) -> list[str]:
    try:
        return [line.split(":", 1)[0] for line in (root / "etc/passwd").read_text().splitlines() if ":" in line]
    except OSError:
        return []


def from_env(kind: Kind, env: dict[str, str] | None, query: str = "", sources: Sources | None = None
             ) -> list[Candidate]:
    """Facts about this box: $USER, the hostname, real users and directories, the shell's places."""
    env = env or {}
    out: list[Candidate] = []
    if kind is Kind.USER:
        who = env.get("USER") or env.get("LOGNAME")
        if who:
            out.append(Candidate(who, "env", "$USER"))
        if query and sources is not None and sources.root is not None:
            said = {w.lower() for w in _words(query)}
            out += [Candidate(u, "env", "a user on this machine", False)
                    for u in _users(sources.root) if u.lower() in said]
    if kind is Kind.HOST:
        try:
            out.append(Candidate(socket.gethostname(), "env", "this host"))
        except OSError:
            pass
    if kind is Kind.CHIP and sources is not None and sources.root is not None:
        # the kernel's names; `sensors` takes `NAME-*` for every chip of that driver
        out += [Candidate(f"{n}-*", "env", f"the {n} sensor chip on this machine", False)
                for n in _chips(sources.root)]
    if kind in PATH_KINDS and sources is not None:
        if sources.home is not None:
            out += [replace(c, strong=False) for c in
                    _named_entries(sources.home, "~/", query, kind, "env", "a directory in your home")]
        if sources.root is not None:
            out += [replace(c, strong=False) for c in
                    _named_entries(sources.root, "/", query, kind, "env", "a directory in /")]
        if kind is not Kind.FILE and (sources.home is not None or sources.cwd is not None):
            out += [Candidate(v, "env", why, False) for v, why in ANCHORS]
    return _dedupe(out)


def _chips(root: Path) -> list[str]:
    """Sensor chip drivers this machine has (`amdgpu`, `k10temp`, `nvme`), from /sys/class/hwmon."""
    names = []
    for f in sorted((root / "sys" / "class" / "hwmon").glob("*/name")):
        try:
            names.append(f.read_text().strip())
        except OSError:
            continue
    return list(dict.fromkeys(n for n in names if n))


def _fits(kind: Kind, value: str) -> bool:
    """A history path is only offered if it still exists and is the right thing.

    Without this, `find big log files` was answered with a `.log` FILE where the
    command wanted a DIRECTORY, because `log` appears somewhere in the string.
    A value that fails this check is not downgraded, it is dropped.
    """
    if kind not in PATH_KINDS:
        return True
    try:
        path = Path(value).expanduser()
        if not path.exists():
            return False
        return (path.is_dir() if kind is Kind.DIR
                else path.is_file() if kind is Kind.FILE else True)
    except OSError:
        return False


def _names(value: str) -> set[str]:
    """The words a path is actually made of. `logs/p7/run.log` does not mean `log`."""
    return {part.lower() for part in re.split(r"[/._-]+", value) if part}


def from_history(kind: Kind, query: str, history: tuple[str, ...]) -> list[Candidate]:
    """Paths and hosts this user has actually used, when the request echoes one.

    The echo has to be a whole component of the path, not a substring of it, and
    for a path the value must still exist and be the kind the token wants.
    """
    if not history or kind not in (*PATH_KINDS, Kind.HOST):
        return []
    words = {w for w in re.findall(r"[\w.-]+", query.lower()) if len(w) > 2}
    if not words:
        return []
    pattern = _PATHISH if kind in PATH_KINDS else _HOSTISH
    out = []
    for line in reversed(history):
        for hit in pattern.findall(line):
            if (_names(hit) & words) and _fits(kind, hit):
                out.append(Candidate(hit, "history", "a path you have used", False))
    return _dedupe(out)[:3]
