"""Headless fixtures. No terminal, no network, no money: the judge is `stub`.

`ui` builds a real `Tui` inside a prompt_toolkit app session backed by a pipe
and a DummyOutput, so `PromptSession` constructs exactly as it does on a tty and
the handlers can be driven by hand.
"""
from __future__ import annotations

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from jiffy.config import Config
from jiffy.graph import api
from jiffy.judge.backends import Stub
from jiffy.types import Doc, Example


@pytest.fixture
def docs() -> dict[str, Doc]:
    return {
        "find": Doc("find", "common", "Find files and directories under a path",
                    (Example("Find files by name", "find . -name '{{pattern}}'"),
                     Example("Find files bigger than a size", "find . -size +100M")), "tldr"),
        "du": Doc("du", "common", "Estimate file space usage",
                  (Example("Show sizes of directories here", "du -sh *"),), "tldr"),
    }


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(judge="stub", usd_cap=0.25, node_timeout_s=5.0,
                  config_path=tmp_path / "config.toml",
                  denylist_path=tmp_path / "denylist.txt",
                  corpus_dir=tmp_path / "corpus")


@pytest.fixture
def answer_fn(docs, cfg):
    """`answer(query)` over the stub judge and the fixture corpus."""
    def run(query, **kw):
        return api.answer(query, cfg=cfg, docs=docs, judge_factory=lambda name: Stub(seed=1),
                          cache=False, **kw)
    return run


@pytest.fixture
def app_session():
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()) as session:
            yield session


class FakeEvent:
    """What a key binding is handed: just the buffer."""

    def __init__(self, buffer: Buffer) -> None:
        self.current_buffer = buffer
        self.app = None


@pytest.fixture
def event():
    return lambda buf: FakeEvent(buf)
