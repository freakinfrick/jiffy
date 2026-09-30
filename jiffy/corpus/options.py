"""Option tables: every flag a command documents, parsed from its man page.

tldr gives 8 hand-picked lines per command; the man page gives all of them.
`ls ~/*.py` is not among the 8, so a line-picker can never ship it. This module
turns `man ls` into a structured table -- flags, the argument each takes, what it
does, and the positional arguments from the synopsis -- so a later node can
build `cmd flags args` from documented parts instead of choosing a whole line.

Reading a man page never runs the command, so it is the source for every
installed name. A command with no man page (fd, bat, uv) falls back to its
`--help` text, run in `helpscan`'s sandbox: no stdin, scrubbed env, throwaway
cwd, a 2 s timeout, and never for a name on its skip list. That fallback happens
only inside `jiffy index --options`; `spec()` at query time reads man pages only.

Cached to `options.json` by `jiffy index --options`; `spec()` falls back to a
live `man` call for a name the cache has not seen.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jiffy.config import CORPUS_DIR

__all__ = ["Option", "Positional", "Spec", "listed", "parse_man", "parse_help", "parse_head", "parse_synopsis",
           "read_man", "read_help", "spec", "scan", "load", "path"]

MAN_WIDTH = 200               # wide enough that no head line wraps
TIMEOUT_S = 5.0
MAX_DESC = 240

#: sections whose `-x` lines are not option definitions
_SKIP_SECTIONS = ("NAME", "SYNOPSIS", "EXAMPLE", "SEE ALSO", "BUGS", "HISTORY", "STANDARDS", "CONFORMING",
                  "ENVIRONMENT", "EXIT STATUS", "RETURN VALUE", "AUTHOR", "REPORTING BUGS", "COPYRIGHT",
                  "FILES", "COLOPHON", "NON-BUGS")
_SECTION = re.compile(r"^[A-Z][A-Z0-9 ,/&()'-]+$")
#: an indented subsection naming one mode: `   QUERY OPTIONS`
_MODE_GROUP = re.compile(r"^([A-Z][A-Z0-9]+) OPTIONS$")
_OVERSTRIKE = re.compile(r".\x08")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: one spelling of a flag: `-a`, `--all`, `-maxdepth`, `--[no-]mailmap`, `-L`
_FLAG = re.compile(r"^(--?(?:\[no-\])?[A-Za-z0-9?@][\w@?.+-]*)")


@dataclass(frozen=True)
class Option:
    flags: tuple[str, ...]          # every spelling, as documented: ("-a", "--all")
    arg: str = ""                   # "SIZE", "levels", "<number>"; "" = takes none
    optional: bool = False          # `--color[=WHEN]`: the argument may be left off
    joiner: str = " "               # how the argument attaches: "=", " " or "" (`-L<n>`)
    desc: str = ""
    values: tuple[str, ...] = ()    # the argument's values, when the page lists them (`listed`)
    needs: str = ""                 # a mode flag this one only works under (`-d` needs `-q`; `_mode`)

    @property
    def short(self) -> str:
        """The spelling a person would type: the shortest one."""
        return min(self.flags, key=len)


@dataclass(frozen=True)
class Positional:
    name: str                       # "FILE", "starting-point", "revision range"
    optional: bool = False
    repeat: bool = False


@dataclass(frozen=True)
class Spec:
    name: str
    synopsis: tuple[str, ...] = ()
    positionals: tuple[Positional, ...] = ()
    options: tuple[Option, ...] = field(default_factory=tuple)
    source: str = "man"

    def option(self, flag: str) -> Option | None:
        return next((o for o in self.options if flag in o.flags), None)


# ---------------------------------------------------------------- one option head


def _expand(flag: str) -> list[str]:
    """`--[no-]mailmap` is two flags."""
    if "[no-]" in flag:
        return [flag.replace("[no-]", ""), flag.replace("[no-]", "no-")]
    return [flag]


def _split_forms(head: str) -> list[str]:
    """`-n <num>, --max-count=<num>` -> two forms. A comma inside <> or [] is not a separator."""
    forms, depth, cur = [], 0, ""
    for i, ch in enumerate(head):
        depth += ch in "<[{"
        depth -= ch in ">]}"
        if ch == "," and depth == 0 and head[i + 1:i + 2] in (" ", "-"):
            forms.append(cur.strip())
            cur = ""
            continue
        cur += ch
    forms.append(cur.strip())
    return [f for f in forms if f]


def parse_head(head: str) -> Option | None:
    """One option head -> Option, or None if it is not one.

    Handles `-a, --all`, `--block-size=SIZE`, `--color[=WHEN]`, `-maxdepth levels`,
    `-size n[cwbkMG]`, `-n <number>, --max-count=<number>`, `-L<start>,<end>:<file>`.
    """
    flags: list[str] = []
    arg, optional, joiner = "", False, " "
    for form in _split_forms(head.strip()):
        m = _FLAG.match(form)
        if not m:
            if form.startswith("-"):        # `-<number>, -n <number>`: skip a form we cannot spell
                continue
            if flags:                       # `-a, then prose`: stop at the first non-flag form
                break
            return None
        flag, rest = m.group(1), form[m.end():]
        if flag.endswith("..."):            # clap's `-v, --verbose...`: repeatable, not part of the name
            flag = flag[:-3]
        if rest.startswith("[="):
            a, o, j = rest[2:].rstrip("]"), True, "="
        elif rest.startswith("="):
            # `--lines=` with nothing after: it takes a value the page did not name
            a, o, j = rest[1:] or flag.lstrip("-"), False, "="
        elif rest.startswith(" "):
            a, o, j = rest.strip(), False, " "
        elif rest.startswith("["):         # `-O[level]`
            a, o, j = rest.strip("[]"), True, ""
        elif rest:
            a, o, j = rest, False, ""
        else:
            a, o, j = "", False, " "
        flags.extend(_expand(flag))
        if a and not arg:
            arg, optional, joiner = a, o, j
    if not flags or any(len(f) < 2 or f in ("-", "--") for f in flags):
        return None
    if len(arg.split()) > 3:                # prose, not an argument name
        return None
    return Option(tuple(dict.fromkeys(flags)), arg, optional, joiner)


# ---------------------------------------------------------------- synopsis


_SYN_TOKEN = re.compile(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\](?:\.\.\.)?|<[^>]+>(?:\.\.\.)?|\S+")
_NOT_POSITIONAL = {"option", "options", "opts", "flags", "--", "expression-options"}


def parse_synopsis(line: str, name: str) -> tuple[Positional, ...]:
    """`ls [OPTION]... [FILE]...` -> (FILE optional repeat). Options and the name are skipped."""
    words = name.split()
    out: list[Positional] = []
    toks = _SYN_TOKEN.findall(line)
    if toks[:len(words)] == words:
        toks = toks[len(words):]
    prev = ""
    for tok in toks:
        after_flag = prev.startswith("-") and "=" not in prev
        prev = tok
        if after_flag:                                  # `-d secs`, `-w [cols]`: the flag's argument
            continue
        repeat = tok.endswith("...")
        tok = tok[:-3] if repeat else tok
        optional = tok.startswith("[")
        inner = tok[1:-1].strip() if optional else tok
        if inner.endswith("..."):
            repeat, inner = True, inner[:-3]
        if "<" in inner and not inner.startswith("-"):
            inner = inner[inner.rindex("<"):]          # `[--] <path>` -> `<path>`
        inner = inner.strip("<>").strip()
        if not inner or inner.startswith("-") or inner.startswith("|") or "|" in inner:
            continue
        if inner.lower() in _NOT_POSITIONAL or inner.lower().rstrip("s") == "option":
            continue
        inner = re.sub(r"\[,.*\]$", "", inner)          # `MODE[,MODE]` -> MODE
        if not re.match(r"^[\w][\w .:/-]*$", inner):
            continue
        out.append(Positional(inner, optional, repeat))
    return tuple(out)


# ---------------------------------------------------------------- whole page


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_man(name: str, text: str) -> Spec | None:
    """Plain man text -> Spec. None when the page documents no options and no synopsis."""
    lines = [_ANSI.sub("", _OVERSTRIKE.sub("", ln)).rstrip() for ln in text.splitlines()]

    # the description column: the commonest indent of lines just under a flag head
    below = Counter()
    for a, b in zip(lines, lines[1:]):
        if a.lstrip().startswith("-") and b.strip() and _indent(b) > _indent(a):
            below[_indent(b)] += 1
    dcol = below.most_common(1)[0][0] if below else 14

    section, synopsis, opts, seen = "", [], [], set()
    mode, leader = "", ""       # `QUERY OPTIONS` led by `-q, --query`: see `_mode`
    stacked: list[Option] = []
    wrapping = False            # `-A` / `-o` / `--print-file-name` over one description
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln and not ln.startswith(" ") and _SECTION.match(ln.strip()):
            section, mode, leader = ln.strip(), "", ""
            i += 1
            continue
        if section != "SYNOPSIS" and 0 < _indent(ln) < dcol and _SECTION.match(ln.strip()):
            m = _MODE_GROUP.match(ln.strip())
            mode, leader = (m.group(1).lower() if m else ""), ""
            i += 1
            continue
        if section == "SYNOPSIS":
            if not ln.strip():
                wrapping = False                          # a blank line ends the entry: prose follows
            elif _indent(ln) < dcol and ln.strip().split()[0] == name.split()[0]:
                synopsis.append(ln.strip())
                wrapping = True
            elif synopsis and wrapping:
                # a wrapped synopsis line: ssh indents it deeper, tree does not. Only a
                # line that starts with the command's name begins a new synopsis entry.
                synopsis[-1] += " " + ln.strip()
        skip = any(section.startswith(s) for s in _SKIP_SECTIONS)
        stripped = ln.lstrip()
        if not skip and stripped.startswith("-") and 0 < _indent(ln) < dcol:
            # `-c     with -lt: ...`: a short head shares its line with the description
            # but `-mtime n` is a head with its argument in the same column: only split
            # on a real gap (2+ spaces) or when what follows reads as prose (3+ words)
            head, desc = ln, ""
            if len(ln) > dcol and ln[dcol - 1] == " " and ln[dcol] != " " and ln[_indent(ln):dcol].strip():
                rest = ln[dcol:].strip()
                if ln[dcol - 2] == " " or len(rest.split()) >= 3:
                    head, desc = ln[:dcol], rest
            opt = parse_head(head)
            if opt:
                j = i + 1
                parts = [desc] if desc else []
                while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) >= dcol):
                    if not lines[j].strip():
                        if parts and (j + 1 >= len(lines) or _indent(lines[j + 1]) < dcol):
                            break
                    else:
                        parts.append(lines[j].strip())
                    j += 1
                nxt = lines[j] if j < len(lines) else ""
                if not parts and j == i + 1 and nxt.lstrip().startswith("-") and _indent(nxt) == _indent(ln):
                    stacked.append(opt)                  # the next head's description is this one's too
                    i = j
                    continue
                if stacked:
                    group = [*stacked, opt]
                    carrier = next((o for o in group if o.arg), opt)
                    opt = Option(tuple(dict.fromkeys(f for o in group for f in o.flags)),
                                 carrier.arg, carrier.optional, carrier.joiner)
                    stacked = []
                needs = ""
                if mode and not leader:
                    leader = _mode(mode, opt)
                    mode = mode if leader else ""
                elif leader:
                    needs = leader
                if not set(opt.flags) & seen:            # the first definition wins (find repeats -name)
                    seen.update(opt.flags)
                    d = " ".join(parts)
                    opts.append(Option(opt.flags, opt.arg, opt.optional, opt.joiner,
                                       d[:MAX_DESC].rstrip() + ("…" if len(d) > MAX_DESC else ""),
                                       listed(opt.arg, d) if opt.arg else (), needs))
                i = j
                continue
        i += 1
    if not opts and not synopsis:
        return None
    pos = parse_synopsis(synopsis[0], name) if synopsis else ()
    return Spec(name, tuple(synopsis), pos, tuple(opts), "man")


def _mode(word: str, first: Option) -> str:
    """The flag that leads a `WORD OPTIONS` group, or "" when the group has none.

    nvidia-smi's `QUERY OPTIONS` opens with `-q, --query`, and `-d TYPE` under it
    only works with `-q`. Only a first option spelled `--WORD` leads: `LIST OPTIONS`
    opens with `--list-gpus`, and `-B` beside it stands alone. `GENERAL OPTIONS`
    opens with `--help`, so it leads nothing.
    """
    return first.short if f"--{word}" in first.flags and not first.arg else ""


#: one value as pages list them: `auto`, `TEMPERATURE`, `github-dark`, `pci.bus_id`
_ITEM = r"[A-Za-z0-9][\w.+-]*"
_SEP = r"(?:\s*,\s*(?:or\s+|and\s+)?|\s+or\s+|\s*\|\s*)"
#: the shapes a page lists an argument's values in. Each captures the list itself.
_LISTS = (
    re.compile(r"\[possible values: ([^\]]+)\]"),                                   # clap
    re.compile(r"\{(" + _ITEM + r"(?:," + _ITEM + r")+)\}"),                        # argparse
    re.compile(r"(?:one of(?: the following)?(?: values)?|possible values are|valid values are"
               r"|values? (?:is|are|can be|may be))\s*:?\s*(" + _ITEM + r"(?:" + _SEP + _ITEM + r")+)"
               r"(?=\s*[.,;:)\]]|\s*$)"),                                                  # ends at punctuation
    re.compile(r"('" + _ITEM + r"'(?:" + _SEP + r"'" + _ITEM + r"')+)"),              # 'auto', 'always' or 'never'
    re.compile(r":\s*([A-Z][A-Z0-9_]+(?:,\s*[A-Z][A-Z0-9_]+){2,})"),                 # info: MEMORY, ECC, POWER
)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": "'", "”": "'", '"': "'", "`": "'"})


def listed(arg: str, desc: str) -> tuple[str, ...]:
    """The values an option's argument takes, when its page lists them; else ().

    Only a list the page writes as one counts, never a word that happens to be in
    the prose: `-d TYPE` on nvidia-smi lists `MEMORY, UTILIZATION, ...`, so its
    blank is offered those and nothing else. A list may be partial (`ACCEPT, DROP,
    ... or a user-defined chain`); a value outside it is the user's to type.
    """
    text = re.sub(r"\s*\([^()]*\)", "", f"{arg} {desc}".translate(_QUOTES))   # `'always' (default), 'auto'`
    for rx in _LISTS:
        m = rx.search(text)
        if m:
            items = [i.strip("'") for i in re.split(_SEP, m.group(1).strip())]
            items = [i for i in items if re.fullmatch(_ITEM, i)]
            if len(items) >= 2:
                return tuple(dict.fromkeys(items))
    return ()


_USAGE = re.compile(r"^\s*usage:\s*(.*)$", re.I)


def parse_help(name: str, text: str) -> Spec | None:
    """`--help` text -> Spec. The flag block reads like a man page's; the synopsis
    is the `Usage:` line (and any aligned `name ...` lines under it)."""
    lines = [_ANSI.sub("", ln).rstrip() for ln in text.splitlines()]
    synopsis: list[str] = []
    for i, ln in enumerate(lines):
        m = _USAGE.match(ln)
        if not m:
            continue
        first = m.group(1).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
        if first:
            synopsis.append(first)
        head = name.split()[0]
        for nxt in lines[i + 2 if not m.group(1).strip() else i + 1:]:
            if not nxt.startswith(" ") or nxt.strip().split()[:1] != [head]:
                break
            synopsis.append(nxt.strip())
        break
    base = parse_man(name, text)
    if base is None and not synopsis:
        return None
    opts = base.options if base else ()
    pos = parse_synopsis(synopsis[0], name) if synopsis else ()
    return Spec(name, tuple(synopsis), pos, opts, "help")


# ---------------------------------------------------------------- reading + cache


def man_name(name: str) -> str:
    """tldr's `git log` is the man page `git-log`."""
    return name.replace(" ", "-")


def read_man(name: str, timeout: float = TIMEOUT_S) -> str:
    """Plain text of `man NAME`, or "" when there is none. Never runs NAME itself."""
    if not shutil.which("man"):
        return ""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "MANWIDTH": str(MAN_WIDTH), "MANPAGER": "cat",
           "PAGER": "cat", "GROFF_NO_SGR": "1", "HOME": os.environ.get("HOME", "/tmp")}
    try:
        r = subprocess.run(["man", "--", man_name(name)], capture_output=True, text=True,
                           timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def read_help(name: str) -> str:
    """`NAME --help` in helpscan's sandbox, or "" when it is skip-listed, absent or misbehaves."""
    from jiffy.corpus import helpscan

    words = name.split()
    if words[0] in helpscan.SKIP_NAMES or not shutil.which(words[0]):
        return ""
    status, out = helpscan.run_argv([*words, "--help"])
    return out if status == "ok" else ""


def path(corpus_dir: Path = CORPUS_DIR) -> Path:
    return Path(corpus_dir) / "options.json"


def _to_json(s: Spec) -> dict:
    return asdict(s)


def _from_json(d: dict) -> Spec:
    return Spec(d["name"], tuple(d["synopsis"]), tuple(Positional(**p) for p in d["positionals"]),
                tuple(Option(tuple(o["flags"]), o["arg"], o["optional"], o["joiner"], o["desc"],
                             tuple(o.get("values", ())), o.get("needs", ""))
                      for o in d["options"]), d.get("source", "man"))


_CACHE: dict[str, dict[str, Spec]] = {}


def load(corpus_dir: Path = CORPUS_DIR) -> dict[str, Spec]:
    key = str(corpus_dir)
    if key not in _CACHE:
        p = path(corpus_dir)
        raw = json.loads(p.read_text()) if p.exists() else {"specs": {}}
        _CACHE[key] = {n: _from_json(d) for n, d in raw["specs"].items()}
    return _CACHE[key]


def spec(name: str, corpus_dir: Path = CORPUS_DIR, live: bool = True) -> Spec | None:
    """The cached table for NAME, else (when `live`) a fresh parse of its man page."""
    hit = load(corpus_dir).get(name)
    if hit is not None or not live:
        return hit
    text = read_man(name)
    return parse_man(name, text) if text else None


def installed(names) -> list[str]:
    """Corpus names whose binary is on PATH (`git log` counts if `git` is)."""
    return sorted(n for n in names if shutil.which(n.split()[0]))


def scan(names, corpus_dir: Path = CORPUS_DIR, workers: int = 8, help_fallback: bool = True,
         log=lambda s: print(s, file=sys.stderr, flush=True)) -> dict:
    """Parse the man page of every name; write options.json. A name with no usable
    man page falls back to its sandboxed `--help` (see `read_help`) when `help_fallback`."""
    todo = list(names)
    log(f"options: {len(todo)} man pages to read")
    specs: dict[str, dict] = {}
    missing: list[str] = []
    t0 = time.time()

    def one(n):
        text = read_man(n)
        s = parse_man(n, text) if text else None
        if s is None and help_fallback:
            text = read_help(n)
            s = parse_help(n, text) if text else None
            if s is not None and not s.options:
                s = None                # a bare usage line teaches no flags
        return n, s

    with ThreadPoolExecutor(workers) as ex:
        for i, (n, s) in enumerate(ex.map(one, todo), 1):
            if s is not None:
                specs[n] = _to_json(s)
            else:
                missing.append(n)
            if i == 50 or i % 250 == 0:
                el = time.time() - t0
                log(f"options: {i}/{len(todo)} done, ETA {(len(todo) - i) * el / i / 60:.1f} min")
    Path(corpus_dir).mkdir(parents=True, exist_ok=True)
    result = {"specs": specs, "missing": sorted(missing), "seconds": round(time.time() - t0, 1)}
    path(corpus_dir).write_text(json.dumps(result))
    _CACHE.pop(str(corpus_dir), None)
    n_opts = sum(len(s["options"]) for s in specs.values())
    result["from_help"] = sum(s["source"] == "help" for s in specs.values())
    log(f"options: {len(specs)} parsed ({n_opts} options, {result['from_help']} from --help), "
        f"{len(missing)} with neither, "
        f"{result['seconds']}s -> {path(corpus_dir)}")
    return result
