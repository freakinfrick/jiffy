"""--help scanner. Runs ONLY from an explicit `jiffy index --help-scan`, never implicitly.

Walks $PATH, runs `<bin> --help` with a short timeout, no stdin, a scrubbed env and
a throwaway cwd, in its own process group so a hang is killed whole. Parses the
synopsis + flag block into Doc(source="help"). Caches to help.json (docs + skips).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jiffy.config import CORPUS_DIR
from jiffy.corpus import machine
from jiffy.types import Doc, Example

MAX_BYTES = 64 * 1024
MAX_EX = 8
TIMEOUT_S = 2.0
# Never probed: interactive, session-taking, or power-state commands, where even --help is a gamble.
SKIP_NAMES = {"reboot", "poweroff", "halt", "shutdown", "init", "telinit", "systemctl", "login", "su", "sudo",
              "passwd", "chsh", "chfn", "newgrp", "sg", "vi", "vim", "nano", "emacs", "top", "htop", "less",
              "more", "screen", "tmux", "ssh", "telnet", "ftp", "sftp", "python", "python3", "node", "irb",
              "bash", "sh", "dash", "zsh", "fish", "gdb", "lldb", "man", "info", "sleep", "cat", "read"}
_FLAG = re.compile(r"^\s{1,12}(-[-\w][^\s,]*(?:[ ,=]+[^\s]+)?(?:,\s*-[-\w][^\s]*(?:[ =]\S+)?)?)\s{2,}(\S.*)$")
_USAGE = re.compile(r"^\s*(?:usage|synopsis)\s*:\s*(.*)$", re.I)


def path(corpus_dir: Path = CORPUS_DIR) -> Path:
    return corpus_dir / "help.json"


def parse_help(name: str, text: str) -> Doc | None:
    """Synopsis line + flag block -> Doc. None if nothing usable came out."""
    lines = text.splitlines()
    usage = ""
    for i, ln in enumerate(lines):
        m = _USAGE.match(ln)
        if m:
            usage = m.group(1).strip()
            if not usage and i + 1 < len(lines):
                usage = lines[i + 1].strip()
            break
    desc = next((ln.strip() for ln in lines if ln.strip() and not _USAGE.match(ln) and not ln.lstrip().startswith("-")
                 and not ln.strip().lower().startswith(("usage", "try "))), "")
    ex: list[Example] = []
    if usage:
        ex.append(Example(f"synopsis of {name}", usage))
    for ln in lines:
        m = _FLAG.match(ln)
        if m and len(ex) < MAX_EX:
            flag = re.split(r",\s*", m.group(1))[-1] if "," in m.group(1) else m.group(1)
            ex.append(Example(m.group(2).strip(), f"{name} {flag.strip()}"))
    if not ex:
        return None
    return Doc(name, "linux", desc[:300], tuple(ex), "help")


def _run(binpath: str) -> tuple[str, str]:
    return run_argv([binpath, "--help"])


def run_argv(argv: list[str]) -> tuple[str, str]:
    """Run argv sandboxed -> (status, output). status: ok | timeout | error."""
    with tempfile.TemporaryDirectory(prefix="jiffy-help-") as tmp:
        env = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "TERM": "dumb", "HOME": tmp, "TMPDIR": tmp}
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env=env, cwd=tmp, start_new_session=True)
        except OSError as e:
            return "error", str(e)
        try:
            out, _ = p.communicate(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
            return "timeout", ""
        return "ok", out[:MAX_BYTES].decode("utf-8", "replace")


def _path_bins() -> dict[str, str]:
    seen: dict[str, str] = {}
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            for e in os.scandir(d):
                if e.name not in seen and e.is_file() and os.access(e.path, os.X_OK):
                    seen[e.name] = e.path
        except OSError:
            continue
    return seen


def load(corpus_dir: Path = CORPUS_DIR) -> dict[str, Doc]:
    p = path(corpus_dir)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {n: Doc(d["name"], d["plat"], d["desc"], tuple(Example(e["d"], e["c"]) for e in d["ex"]), "help")
            for n, d in raw["docs"].items()}


def scan(corpus_dir: Path = CORPUS_DIR, force: bool = False, workers: int = 8, limit: int | None = None,
         log=lambda s: print(s, file=sys.stderr, flush=True)) -> dict:
    """Probe PATH binaries; write help.json. Skips names the machine index covers unless force."""
    covered = set() if force else set(machine.load(corpus_dir))
    bins = {n: b for n, b in _path_bins().items() if n not in covered}
    skips: dict[str, str] = {n: "in-skip-list" for n in bins if n in SKIP_NAMES}
    todo = [(n, b) for n, b in sorted(bins.items()) if n not in skips][:limit]
    log(f"helpscan: {len(todo)} binaries to probe ({len(covered)} already indexed, {len(skips)} skip-listed)")
    docs: dict[str, dict] = {}
    t0 = time.time()

    def one(nb):
        n, b = nb
        st, out = _run(b)
        if st != "ok":
            return n, None, st
        d = parse_help(n, out)
        return n, d, "ok" if d else "unparsed"

    with ThreadPoolExecutor(workers) as ex:
        for i, (n, d, st) in enumerate(ex.map(one, todo), 1):
            if d:
                docs[n] = {"name": d.name, "plat": d.plat, "desc": d.desc,
                           "ex": [{"d": e.does, "c": e.line} for e in d.examples]}
            else:
                skips[n] = st
            if i == 20 or i % 200 == 0:
                el = time.time() - t0
                log(f"helpscan: {i}/{len(todo)} done, ETA {(len(todo) - i) * el / i / 60:.1f} min")
    corpus_dir.mkdir(parents=True, exist_ok=True)
    result = {"docs": docs, "skipped": skips, "scanned": len(todo), "seconds": round(time.time() - t0, 1)}
    path(corpus_dir).write_text(json.dumps(result))
    log(f"helpscan: {len(docs)} indexed, {len(skips)} skipped in {result['seconds']}s -> {path(corpus_dir)}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    r = scan(force=a.force, limit=a.limit, workers=a.workers)
    print(json.dumps({k: (len(v) if isinstance(v, dict) else v) for k, v in r.items()}))
