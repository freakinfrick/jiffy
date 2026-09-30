"""Fixtures for the graph tests. No network, no money: every judge here is local.

`Scripted` is the lever the pipeline tests need — a judge whose probabilities are
dictated by the test, so a tournament winner or a critic veto can be *set* rather
than fished for out of the stub's hash.
"""
from __future__ import annotations

import pytest

from jiffy.config import Config
from jiffy.judge.backends import _Batching
from jiffy.judge.base import JudgeAdapter
from jiffy.judge.errors import JudgeUnavailable
from jiffy.types import Doc, Example


class Scripted(_Batching, JudgeAdapter):
    """Judge with the answers written down. `paid` makes it meter like the API."""

    paid = False

    def __init__(self, choices: dict[str, float] | None = None, noul: float = 0.9,
                 nouls: dict[str, float] | None = None, raises: bool = False,
                 usd_per_call: float = 0.0, order_sensitive: bool = False,
                 fail_on: str | None = None) -> None:
        super().__init__()
        self.choices = choices or {}
        self.default_noul = noul
        self.nouls = nouls or {}
        self.raises = raises
        self.usd_per_call = usd_per_call
        self.order_sensitive = order_sensitive
        self.fail_on = fail_on
        self.calls = 0

    def _tick(self, instructions: str = "") -> None:
        self.calls += 1
        self.cost_usd += self.usd_per_call
        if self.raises or (self.fail_on and self.fail_on in instructions):
            raise JudgeUnavailable("scripted failure")

    def choice(self, state, instructions, options):
        self._tick(instructions)
        options = list(options)
        names = _names(state, options)
        w = {}
        for i, (key, name) in enumerate(zip(options, names)):
            base = self.choices.get(name, 1.0)
            if self.order_sensitive:      # first position gets a thumb on the scale
                base *= 2.0 if i == 0 else 1.0
            w[key] = base
        t = sum(w.values()) or 1.0
        return {k: v / t for k, v in w.items()}

    def noul(self, state, instructions, yes=None, no=None):
        self._tick(instructions)
        key = (state.get("command", {}).get("name") if isinstance(state.get("command"), dict)
               else state.get("command")) or state.get("command_line", "")
        line = state.get("invocation", {}).get("line", "") if isinstance(state.get("invocation"), dict) else ""
        for probe in (line, key):
            if probe in self.nouls:
                return self.nouls[probe]
        return self.default_noul


def _names(state: dict, options: list[str]) -> list[str]:
    """Map c0/c1/... back to command names, e0/e1/... to lines, v0/v1/... to values, for scripting."""
    tourney = state.get("candidates")          # the tournament: "name: desc" strings
    if isinstance(tourney, dict):
        return [str(tourney.get(k, k)).split(":", 1)[0] for k in options]
    vals = state.get("values")                 # fill: v0/v1/... -> the proposed value, `none` stays
    if isinstance(vals, dict):
        return [k if k == "none" else str(vals.get(k, k)).split("  (")[0] for k in options]
    cmds = state.get("commands")
    if isinstance(cmds, dict):
        return [cmds.get(k, {}).get("name", k) for k in options]
    inv = state.get("invocations")
    if isinstance(inv, dict):
        return [inv.get(k, {}).get("line", k) for k in options]
    return list(options)


@pytest.fixture
def docs() -> dict[str, Doc]:
    return {
        "find": Doc("find", "common", "Find files and directories under a path",
                    (Example("Find files by name", "find . -name '{{pattern}}'"),
                     Example("Find files bigger than a size", "find . -size +100M")), "tldr"),
        "grep": Doc("grep", "common", "Search text inside files",
                    (Example("Search a pattern recursively", "grep -r '{{pat}}' ."),), "tldr"),
        "du": Doc("du", "common", "Estimate file space usage",
                  (Example("Show sizes of directories here", "du -sh *"),), "tldr"),
        "rm": Doc("rm", "common", "Remove files and directories",
                  (Example("Delete a directory and its contents", "rm -rf {{dir}}"),), "tldr"),
    }


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config that touches nothing real: no writes to ~/.config, no user denylist."""
    return Config(judge="stub", usd_cap=0.25, node_timeout_s=5.0,
                  config_path=tmp_path / "config.toml",
                  denylist_path=tmp_path / "denylist.txt",
                  corpus_dir=tmp_path / "corpus")


@pytest.fixture
def judge_factory():
    """`lambda name: adapter`, with one adapter instance cached per backend name."""
    def make(adapter_for):
        made: dict[str, JudgeAdapter] = {}
        def factory(name: str) -> JudgeAdapter:
            if name not in made:
                made[name] = adapter_for(name)
            return made[name]
        factory.made = made           # type: ignore[attr-defined]
        return factory
    return make
