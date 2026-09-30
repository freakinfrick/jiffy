"""`jiffy` — argument surface. Bare is the TUI; everything else is a subcommand.

    jiffy                      the prompt
    jiffy "rename a file"      one answer, printed (with --explain: the per-stage trace)
    jiffy index [--help-scan]  index this machine's commands, optionally probe PATH --help
    jiffy bench ...            the honesty instrument (flags parsed by jiffy.bench)
    jiffy learn ...            the explicit, idempotent reranking phase
    jiffy config               where config lives and what it resolved to

Each subcommand owns its own flags: `bench` and `learn` are passthroughs —
everything after the subcommand name goes verbatim to that module's `main(argv)`,
so this file never re-declares (and never drifts from) their arguments.

Global flags (`--judge`, `--explain`) work before the subcommand. `--judge` is
applied through the documented env override (`JIFFY_JUDGE`) so there is exactly
one precedence order — file, then env — and the CLI does not become a second one.

Answering needs a Jev key. Without one, `jiffy` and `jiffy QUERY` exit with one
line saying where to put it; `index`, `config` and a dry bench run do not need it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

__all__ = ["main", "build_parser"]


def _globals() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--judge", metavar="NAME", help="the judge backend (default: jev)")
    p.add_argument("--explain", action="store_true", help="print the per-stage trace for the answer")
    return p


def build_parser() -> argparse.ArgumentParser:
    g = _globals()
    ap = argparse.ArgumentParser(prog="jiffy", parents=[g],
                                 description="A smart terminal assistant: CLI commands on demand, from plain English.")
    sub = ap.add_subparsers(dest="cmd")

    idx = sub.add_parser("index", parents=[g], help="index the commands this machine has")
    idx.add_argument("--help-scan", action="store_true",
                     help="probe PATH binaries for --help text (explicit only, never implicit)")
    idx.add_argument("--options", action="store_true",
                     help="parse the man page of every installed corpus command into options.json; "
                          "a command with no man page falls back to its sandboxed --help")
    idx.add_argument("--no-help-fallback", action="store_true",
                     help="with --options: read man pages only, never run a binary")
    idx.add_argument("--tldr-zip", type=Path, default=None,
                     help="offline copy of the pinned tldr release (tldr-pages.en.zip); default downloads it")
    idx.add_argument("--force", action="store_true", help="rescan names the machine index already covers")
    idx.add_argument("--limit", type=int, default=None, help="probe at most N binaries")
    idx.add_argument("--workers", type=int, default=8)

    # Passthroughs: listed for `jiffy --help`, never parsed here (see PASSTHROUGH).
    sub.add_parser("bench", add_help=False,
                   help="score the pipeline over the bench cases (flags: jiffy.bench)")
    sub.add_parser("learn", add_help=False,
                   help="rebuild the local reranking artifact from accepted autofills")

    cfg = sub.add_parser("config", parents=[g], help="show the resolved config")
    cfg.add_argument("--path", action="store_true", help="print the config file path only")
    return ap


def _apply_env(args: argparse.Namespace) -> None:
    if getattr(args, "judge", None):
        os.environ["JIFFY_JUDGE"] = args.judge


def _missing_key(cfg) -> str:
    """One line on how to add the Jev key, or "" when answering can proceed."""
    from jiffy.graph.pipeline import DEFAULT_JUDGE
    from jiffy.judge.backends import KEYFILE
    if ((cfg.judge or "").strip() or DEFAULT_JUDGE) != "jev":
        return ""
    try:
        if KEYFILE.read_text().strip():
            return ""
    except OSError:
        pass
    return (f"jiffy needs a Jev (TypeSafe) API key: save it to {KEYFILE} "
            f"(mkdir -p {KEYFILE.parent} && echo YOUR_KEY > {KEYFILE})")


# ---------------------------------------------------------------- subcommands


def _cmd_index(args, cfg) -> int:
    from jiffy.corpus import helpscan, machine
    from jiffy.graph import api

    meta = machine.build(cfg.corpus_dir, tldr_zip=args.tldr_zip)
    if not meta["counts"]["man"]:
        print("no installed command has a man page here (is man-db installed?)", file=sys.stderr)
        return 2
    print(f"machine index: {meta['counts']['commands']} commands on {meta['os'].get('PRETTY_NAME', '?')}, "
          f"tldr {meta['tldr'] or 'unavailable'} -> {machine.path(cfg.corpus_dir)}")
    if args.help_scan:
        result = helpscan.scan(cfg.corpus_dir, force=args.force,
                               workers=args.workers, limit=args.limit)
        print(f"help corpus: {len(result['docs'])} parsed, "
              f"{len(result['skipped'])} skipped, {result['seconds']}s")
    api.reset_cache()
    if args.options:
        from jiffy.corpus import options
        names = options.installed(api.corpus(cfg))[:args.limit]
        result = options.scan(names, cfg.corpus_dir, workers=args.workers,
                              help_fallback=not args.no_help_fallback)
        print(f"options: {len(result['specs'])} commands ({result['from_help']} from --help), "
              f"{len(result['missing'])} with neither man page nor --help, "
              f"{result['seconds']}s")
    return 0


def _passthrough(name: str, rest: list[str]) -> int:
    """Hand `rest` to that unit's own `main(argv)`. Its flags are its business."""
    import importlib

    try:
        mod = importlib.import_module(f"jiffy.{name}")
        entry = mod.main
    except (ModuleNotFoundError, AttributeError):
        if name == "learn":
            print("`jiffy learn` needs accepted-autofill records; persistence is off by "
                  "default (set persistence.accepted = true in the config).", file=sys.stderr)
        else:
            print(f"jiffy.{name} exposes no main(argv)", file=sys.stderr)
        return 2
    return int(entry(rest) or 0)


def _cmd_config(args, cfg) -> int:
    if args.path:
        print(cfg.config_path)
        return 0
    print(f"config    {cfg.config_path}")
    print(f"denylist  {cfg.denylist_path}")
    print(f"corpus    {cfg.corpus_dir}")
    print(f"judge     {cfg.judge or 'jev'}")
    print(f"usd_cap   ${cfg.usd_cap:.2f}/day")
    print(f"timeouts  stage {cfg.node_timeout_s}s / http {cfg.http_timeout_s}s")
    p = cfg.persistence
    print(f"persist   history={p.history} accepted={p.accepted} aliases={p.aliases}")
    return 0


#: subcommands whose arguments belong to another unit, not to this parser
PASSTHROUGH = ("bench", "learn")


SUBCOMMANDS = ("index", "config", *PASSTHROUGH)


def _split(argv: list[str]) -> tuple[list[str], str | None, list[str]]:
    """(globals before the subcommand, passthrough name or None, its argv)."""
    for i, tok in enumerate(argv):
        if tok in PASSTHROUGH:
            return argv[:i], tok, argv[i + 1:]
        if not tok.startswith("-") and tok in ("index", "config"):
            break
    return argv, None, []


def _query(argv: list[str]) -> tuple[list[str], str]:
    """(flags, query) when the first word is not a subcommand: `jiffy [--explain] rename a file`."""
    words = [t for t in argv if not t.startswith("-")]
    if not words or words[0] in SUBCOMMANDS:
        return argv, ""
    flags, rest, i = [], [], 0
    while i < len(argv):
        t = argv[i]
        if t == "--judge" and i + 1 < len(argv):
            flags += argv[i:i + 2]
            i += 2
            continue
        (flags if t.startswith("-") else rest).append(t)
        i += 1
    return flags, " ".join(rest)


def _cmd_ask(query: str, explain: bool) -> int:
    """One answer, printed. The gate's verdict is shown; nothing is ever run."""
    from jiffy.graph import answer
    ans = answer(query)
    if not ans.assembled.line:
        down = [n for n in ans.trace.nodes if n.degraded]
        print(f"judge unavailable: {down[0].detail}" if down else "no command found for that", file=sys.stderr)
        return 1
    print(ans.assembled.comment_line)
    if ans.verdict.tier.name != "CLEAN":
        print(f"[{ans.verdict.tier.name}] {ans.verdict.reason}")
    if explain:
        print(ans.trace.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    head, through, rest = _split(argv)
    flags, query = _query(argv) if through is None else (argv, "")
    args = build_parser().parse_args(head if through else flags)
    _apply_env(args)

    from jiffy.config import load as load_config
    cfg = load_config()

    if through is not None:
        return _passthrough(through, rest)
    if args.cmd == "index":
        return _cmd_index(args, cfg)
    if args.cmd == "config":
        return _cmd_config(args, cfg)

    missing = _missing_key(cfg)
    if missing:
        print(missing, file=sys.stderr)
        return 2
    if query:
        return _cmd_ask(query, args.explain)
    from jiffy.tui import run as run_tui
    return run_tui(cfg=cfg, explain=args.explain)


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
