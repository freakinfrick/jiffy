"""A DENY verdict has no code path that reaches execution.

Three independent refusals, tested independently: the chokepoint
(`shell.execute`), the ENTER handler, and the prompt's validator. Plus a
structural check that `subprocess` lives in exactly one module of this package.
"""
from __future__ import annotations

import pathlib

from prompt_toolkit.buffer import Buffer

from jiffy import gate
from jiffy.graph.assemble import assemble
from jiffy.tui import shell
from jiffy.tui.app import Tui
from jiffy.types import Answer, Candidate, Doc, Tier, Trace

DENIED = "rm -rf /"
DOC = Doc("rm", "common", "Remove files", (), "tldr")


def _denied_answer(query="delete everything"):
    asm = assemble(DENIED, query)
    verdict = gate.evaluate(asm.line)
    assert verdict.tier == Tier.DENY, verdict
    return Answer(assembled=asm, candidate=Candidate("rm", DOC),
                  alternates=[], verdict=verdict,
                  trace=Trace(query=query))


def test_gate_agrees_this_line_is_deny():
    assert gate.evaluate(DENIED).tier == Tier.DENY
    assert gate.may_run(gate.evaluate(DENIED), confirmed=True) is False


def test_chokepoint_refuses_denied_lines_with_and_without_confirmation():
    ran: list[str] = []
    verdict = gate.evaluate(DENIED)
    for confirmed in (False, True):
        res = shell.execute(DENIED, verdict, confirmed=confirmed,
                            runner=lambda line: ran.append(line) or 0)
        assert isinstance(res, shell.Refusal)
        assert "DENIED" in res.reason
    assert ran == []


def _ui(app_session, cfg, answer):
    ui = Tui(cfg=cfg, answer_fn=lambda q, **kw: answer)
    ui.engine.stop()
    ui.engine.turn = None
    return ui


def test_enter_never_schedules_a_denied_line(app_session, cfg, event):
    from jiffy.tui.engine import Turn, options_for

    ans = _denied_answer()
    ui = _ui(app_session, cfg, ans)
    ui.engine.turn = Turn("delete everything", ans, options_for(ans, "delete everything"))
    buf = Buffer()
    buf.text = "delete everything"

    ran: list[str] = []
    ui.execute = lambda *a, **k: ran.append(a) or 0

    for _ in range(3):                      # every ENTER, including a "second" one
        ui.on_enter(event(buf))
    assert ui.pending_run is None           # nothing was ever handed to the loop
    assert ui.confirmed is False            # a DENY is not a CONFIRM in disguise
    assert ran == []


def test_validator_rejects_a_denied_line(app_session, cfg):
    ui = _ui(app_session, cfg, _denied_answer())
    assert ui._valid(DENIED) is False
    assert ui._valid("mkfs.ext4 /dev/sda") is False
    assert ui._valid("ls -la") is True


def test_subprocess_is_confined_to_the_shell_module():
    pkg = pathlib.Path(__file__).resolve().parents[2] / "jiffy" / "tui"
    users = sorted(p.name for p in pkg.glob("*.py") if "subprocess" in p.read_text())
    assert users == ["shell.py"]


def test_end_to_end_a_denied_winner_is_shown_struck_through_and_never_runs(
        app_session, cfg, docs, event):
    """The whole path: stub judge -> pipeline -> gate -> banner. Nothing executes."""
    from jiffy.graph import api
    from jiffy.judge.backends import Stub
    from jiffy.tui import render
    from jiffy.tui.engine import Turn, options_for

    query = "delete everything under root"
    corpus = dict(docs)
    from jiffy.types import Example
    corpus["rm"] = Doc("rm", "common", "Remove files and directories",
                       (Example("Delete everything under root", DENIED),), "tldr")
    ans = api.answer(query, cfg=cfg, docs=corpus, judge_factory=lambda name: Stub(seed=1), cache=False)
    opts = options_for(ans, query)
    denied = [o for o in opts if o.verdict.tier == Tier.DENY]
    assert denied, [(o.line, o.verdict.tier) for o in opts]

    ui = Tui(cfg=cfg, answer_fn=lambda q, **kw: ans)
    ui.engine.stop()
    ui.engine.turn = Turn(query, ans, opts)
    ui.engine.turn.index = opts.index(denied[0])

    rows = list(render.banner(denied[0].assembled.line, denied[0].verdict))
    struck = [style for style, text in rows if text == denied[0].assembled.line]
    assert struck == ["class:jiffy.deny"]
    assert "strike" in dict(render.STYLE.style_rules)["jiffy.deny"]
    assert any("DENIED" in text for _style, text in rows)

    ran: list = []
    ui.execute = lambda *a, **k: ran.append(a) or 0
    buf = Buffer()
    buf.text = query
    ui.on_tab(event(buf))
    for _ in range(2):
        ui.on_enter(event(buf))
    assert ui.pending_run is None and ran == []
