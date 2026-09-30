"""Config: `~/.config/jiffy/config.toml`, defaults baked in, env overrides.

This module is the ONLY thing in jiffy that writes to `~/.config/jiffy/`, and it
writes exactly one file, exactly once: a commented default config on first run.
Persistence is off everywhere by default (spec: "Persistence — everything off by
default"); nothing else touches disk unless a setting is toggled on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:                      # 3.11+
    import tomllib
except ModuleNotFoundError:   # 3.10
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["Config", "Persistence", "CONFIG_DIR", "CONFIG_PATH", "DEFAULT_TOML", "load"]

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jiffy"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DENYLIST_PATH = CONFIG_DIR / "denylist.txt"
CORPUS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "jiffy" / "corpus"


@dataclass(frozen=True)
class Persistence:
    """All False. Each is an explicit user opt-in."""

    history: bool = False
    accepted: bool = False
    aliases: bool = False
    cache: bool = False


@dataclass(frozen=True)
class Config:
    judge: str = "jev"                 # the one backend every stage asks; "" means jev too
    usd_cap: float = 1.00              # per DAY, jiffy's OWN ledger; never inherits or mutates jev-probes'
    node_timeout_s: float = 8.0        # per judged pipeline stage
    http_timeout_s: float = 20.0       # per backend request
    workdir: str = "~"                 # where commands run; "." = wherever jiffy was launched
    persistence: Persistence = field(default_factory=Persistence)
    config_path: Path = CONFIG_PATH
    denylist_path: Path = DENYLIST_PATH
    corpus_dir: Path = CORPUS_DIR


DEFAULT_TOML = """\
# jiffy config. Written once on first run; jiffy never rewrites it.
# Env overrides: JIFFY_JUDGE, JIFFY_WORKDIR.

# The judge every pipeline stage asks. Jev (paid) is the only one today; it needs
# a key in ~/.config/typesafe/api_key. Spend is capped per day by usd_cap.
judge = "jev"

# jiffy's own spend cap, in USD per day. Independent of the jev-probes ledger.
usd_cap = 1.00

# Per judged stage, and per HTTP request. A stage that times out degrades (keeps
# what the stage before it produced); it never fails the run.
node_timeout_s = 8.0
http_timeout_s = 20.0

# Where commands run, and what "here" means when filling a path. "~" by default,
# so `tree -L 2` shows your home, not whichever repo you launched jiffy from.
# "." keeps the directory jiffy was started in.
workdir = "~"

[persistence]
# Everything off. Nothing is written outside ~/.config/jiffy and the corpus cache
# until one of these is true.
history = false
accepted = false
aliases = false
# cache: keep judge answers on disk between runs. Off by default because it
# writes a file per adapter; turn it on to stop paying twice for the same query.
cache = false
"""


def _write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML)


def load(path: Path | None = None, write_default: bool = True) -> Config:
    """Read config, applying file then env. Writes the commented default on first run."""
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        if write_default:
            _write_default(path)
        raw: dict[str, Any] = {}
    else:
        raw = tomllib.loads(path.read_text())

    p = raw.get("persistence", {})
    cfg = Config(
        judge=str(raw.get("judge", Config.judge)),
        usd_cap=float(raw.get("usd_cap", Config.usd_cap)),
        node_timeout_s=float(raw.get("node_timeout_s", Config.node_timeout_s)),
        http_timeout_s=float(raw.get("http_timeout_s", Config.http_timeout_s)),
        workdir=str(raw.get("workdir", Config.workdir)),
        persistence=Persistence(
            history=bool(p.get("history", False)),
            accepted=bool(p.get("accepted", False)),
            aliases=bool(p.get("aliases", False)),
            cache=bool(p.get("cache", False)),
        ),
        config_path=path,
    )

    env: dict[str, Any] = {}
    if v := os.environ.get("JIFFY_JUDGE"):
        env["judge"] = v
    if v := os.environ.get("JIFFY_WORKDIR"):
        env["workdir"] = v
    return replace(cfg, **env) if env else cfg
