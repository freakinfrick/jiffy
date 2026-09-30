"""The machine index: the commands THIS machine has, described by its own docs.

`jiffy index` builds it and `load` reads it. Sources, all local except tldr:

    man -k      every section 1/8 page whose command is installed. A page for a
                subcommand (`git-log`) counts when its head (`git`) is installed,
                and is keyed the way it is typed and the way tldr titles it (`git log`).
    builtins    the shell's own commands (`history`, `cd`), described by `help -d`.
    tldr        examples for installed commands only, from the pinned release.

Debian 12, Ubuntu 22.04 and Ubuntu 24.04 differ in what they ship, so each gets a
different index. The file records the OS it was built on; `load` warns when the
OS it runs on is no longer that one. The index lives in the corpus cache
(`~/.local/share/jiffy/corpus/machine.json`), never in the repo or the package.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jiffy.config import CORPUS_DIR
from jiffy.corpus import tldr
from jiffy.types import Doc, Example

SECTIONS = ("1", "8")
_WHATIS = re.compile(r"^(\S+)\s+\((\w+)\)\s+-\s+(.*)$")
_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}
OS_RELEASE = Path("/etc/os-release")


def path(corpus_dir: Path = CORPUS_DIR) -> Path:
    return corpus_dir / "machine.json"


def os_release(p: Path = OS_RELEASE) -> dict[str, str]:
    """ID, VERSION_ID and PRETTY_NAME of the running OS; {} if unreadable."""
    try:
        text = p.read_text()
    except OSError:
        return {}
    kv = dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln)
    return {k: kv[k].strip().strip('"') for k in ("ID", "VERSION_ID", "PRETTY_NAME") if k in kv}


def _run(argv: list[str], timeout: float = 30.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=_ENV,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def typed_name(name: str, which=shutil.which) -> str | None:
    """How a man page's command is typed, or None if this machine cannot run it.

    `ls` -> `ls`; `git-log` -> `git log` when git is installed but git-log is not
    on PATH; `apt-get` stays `apt-get` because it is itself on PATH.
    """
    if which(name):
        return name
    head, sep, rest = name.partition("-")
    if sep and rest and which(head):
        return f"{head} {rest.replace('-', ' ')}"
    return None


def whatis(text: str, which=shutil.which) -> dict[str, str]:
    """`man -k .` output -> {typed name: one-line description} for installed commands."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _WHATIS.match(line.strip())
        if not m or m.group(2)[0] not in SECTIONS:
            continue
        name = typed_name(m.group(1), which)
        if name and name not in out:
            out[name] = m.group(3).strip()
    return out


def builtins(text: str) -> dict[str, str]:
    """`help -d` output for every builtin -> {name: description}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, desc = line.partition(" - ")
        if sep and name.strip() and " " not in name.strip():
            out[name.strip()] = desc.strip()
    return out


def merge(man: dict[str, str], shell: dict[str, str], pages: dict[str, Doc],
          which=shutil.which) -> dict[str, Doc]:
    """One Doc per installed command. tldr supplies examples and its description;
    the man one-liner is kept alongside it, since it often names what tldr does not
    (`mv - move (rename) files`)."""
    docs: dict[str, Doc] = {}
    for name, desc in {**shell, **man}.items():
        src = "man" if name in man else "builtin"
        page = pages.get(name)
        if page is None:
            docs[name] = Doc(name, src, desc, (), src)
        else:
            both = page.desc if desc.lower() in page.desc.lower() else f"{page.desc} ({desc})"
            docs[name] = Doc(name, page.plat, both, page.examples, "tldr")
    for name, page in pages.items():           # installed, but no man page (fd, uv)
        if name not in docs and which(name.split()[0]):
            docs[name] = page
    return docs


def build(corpus_dir: Path = CORPUS_DIR, tldr_zip: Path | None = None,
          log=lambda s: print(s, file=sys.stderr, flush=True)) -> dict:
    """Index this machine and write machine.json. Returns the metadata."""
    t0 = time.time()
    man = whatis(_run(["man", "-k", "."]))
    shell = builtins(_run(["bash", "-c", "help -d $(compgen -b)"]))
    log(f"machine: {len(man)} installed commands with man pages, {len(shell)} shell builtins")
    try:
        pages = tldr.pages(tldr.fetch(corpus_dir, src=tldr_zip))
        tldr_version = tldr.VERSION
    except (OSError, ValueError) as exc:          # offline or a bad copy: man pages still index
        log(f"machine: tldr unavailable ({exc}); indexing without examples")
        pages, tldr_version = {}, None
    docs = merge(man, shell, pages)
    meta = {"os": os_release(), "tldr": tldr_version, "built": time.strftime("%Y-%m-%d %H:%M:%S"),
            "counts": {"commands": len(docs), "man": len(man), "builtins": len(shell),
                       "with_examples": sum(1 for d in docs.values() if d.examples)},
            "seconds": round(time.time() - t0, 1)}
    raw = {n: {"plat": d.plat, "desc": d.desc, "source": d.source,
               "ex": [{"d": e.does, "c": e.line} for e in d.examples]} for n, d in docs.items()}
    corpus_dir.mkdir(parents=True, exist_ok=True)
    path(corpus_dir).write_text(json.dumps({"meta": meta, "docs": raw}))
    log(f"machine: {meta['counts']['commands']} commands, {meta['counts']['with_examples']} with "
        f"tldr examples, {meta['seconds']}s -> {path(corpus_dir)}")
    return meta


def stale(meta: dict, current: dict[str, str] | None = None) -> str:
    """Why the index no longer describes this machine, or "" if it still does."""
    now = os_release() if current is None else current
    built = meta.get("os", {})
    if built and now and (built.get("ID"), built.get("VERSION_ID")) != (now.get("ID"), now.get("VERSION_ID")):
        return (f"index built on {built.get('PRETTY_NAME', '?')}, running on "
                f"{now.get('PRETTY_NAME', '?')}: run `jiffy index`")
    return ""


def load(corpus_dir: Path = CORPUS_DIR, warn=lambda s: print(f"jiffy: {s}", file=sys.stderr)) -> dict[str, Doc]:
    """The machine index, or {} if `jiffy index` has not been run."""
    p = path(corpus_dir)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    if why := stale(raw.get("meta", {})):
        warn(why)
    return {n: Doc(n, d["plat"], d["desc"], tuple(Example(e["d"], e["c"]) for e in d["ex"]), d["source"])
            for n, d in raw["docs"].items()}
