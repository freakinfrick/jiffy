"""Synthetic corpus + stub judges: the bench tests never touch the network or the real corpus."""
from __future__ import annotations

import json

import pytest

from jiffy.config import Config
from jiffy.judge.backends import Stub
from jiffy.types import Doc, Example


def _doc(name: str, desc: str) -> Doc:
    return Doc(name, "common", desc, [Example(f"{desc} example", f"{name} {{{{file}}}}")], "tldr")


@pytest.fixture
def docs():
    d = {"alpha": _doc("alpha", "list files in a folder"), "beta": _doc("beta", "search text in files")}
    for i in range(40):        # decoys: the tournament must really narrow 42 commands to a few finalists
        d[f"decoy{i}"] = _doc(f"decoy{i}", f"list search text files folder variant{i}")
    return d


@pytest.fixture
def cases_file(tmp_path):
    cases = [{"q": "list files in a folder", "cmd": "alpha"},
             {"q": "search text in files", "cmd": ["beta", "gamma"]},
             {"q": "list files folder variant7", "cmd": "decoy7"}]
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases))
    return p


@pytest.fixture
def cfg(tmp_path):
    """A free judge: runs cost nothing and need no --all. Paid-path tests set judge="jev"."""
    return Config(judge="stub", usd_cap=0.25, node_timeout_s=5.0, corpus_dir=tmp_path)


@pytest.fixture
def stub_factory():
    return lambda name: Stub(seed=1)


@pytest.fixture(autouse=True)
def fresh_ledger(tmp_path, monkeypatch):
    """The real ledger is a lifetime total; a test must never read (or trip on) it."""
    import jiffy.judge._http as http
    monkeypatch.setattr(http, "LEDGER_PATH", tmp_path / "ledger.json")
