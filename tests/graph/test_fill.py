"""Placement is the point: the gate grades the line that will actually run."""
from __future__ import annotations

from jiffy.graph import Pipeline, STAGES
from jiffy.types import Doc, Example, Tier

from conftest import Scripted


def _run(docs, cfg, query, judge=None):
    return Pipeline(docs, cfg=cfg, judge_factory=lambda n: judge or Scripted(noul=0.9)).run(query)


def test_nothing_may_come_between_filling_and_grading():
    assert [name for name, _, _ in STAGES][-2:] == ["fill", "gate"]


def test_the_gate_grades_the_filled_line_not_the_template(cfg):
    """`rm -rf {{path}}` is harmless-looking; `rm -rf ~` is not. The gate must see the latter."""
    docs = {"rm": Doc("rm", "common", "remove files",
                      (Example("remove a directory", "rm -rf {{path/to/directory}}"),), "tldr")}
    a = _run(docs, cfg, "delete my home directory", Scripted(noul=0.9, nouls={"rm -rf ~": 0.0}, choices={"~": 9.0}))
    assert a.assembled.line == "rm -rf ~"
    assert a.verdict.tier >= Tier.CONFIRM          # graded on the real thing, by pattern alone


def test_a_line_with_nothing_to_fill_is_untouched(cfg):
    docs = {"ls": Doc("ls", "common", "list files", (Example("list", "ls -la"),), "tldr")}
    a = _run(docs, cfg, "list everything", Scripted(noul=0.9, nouls={"ls -la": 0.9}))
    assert a.assembled.line == "ls -la"
    assert a.assembled.fills == ()


def test_the_fill_stage_reports_what_it_did(cfg):
    docs = {"du": Doc("du", "common", "disk usage",
                      (Example("size of a directory", "du -sh {{path/to/directory}}"),), "tldr")}
    a = _run(docs, cfg, "size of the home directory", Scripted(noul=0.9, choices={"~": 9.0}))
    node = next(n for n in a.trace.nodes if n.kind == "fill")
    assert "filled=1" in node.detail and "path/to/directory=~" in node.detail


def test_the_judge_picks_and_code_writes():
    """The judge sees only strings code built, plus `none`."""
    assert dict((n, judged) for n, _, judged in STAGES)["fill"] is True


def test_the_judge_may_leave_a_blank(cfg):
    docs = {"rm": Doc("rm", "common", "remove files",
                      (Example("remove a directory", "rm -rf {{path/to/directory}}"),), "tldr")}
    a = _run(docs, cfg, "delete the thing", Scripted(noul=0.9, choices={"none": 9.0}))
    assert a.assembled.line == "rm -rf {{path/to/directory}}"


def test_a_failed_fill_judge_keeps_the_code_only_fill(cfg):
    docs = {"du": Doc("du", "common", "disk usage",
                      (Example("size of a directory", "du -sh {{path/to/directory}}"),), "tldr")}
    a = _run(docs, cfg, "size of /srv and the home directory", Scripted(noul=0.9, fail_on="blank"))
    assert a.assembled.line == "du -sh /srv"          # the typed path; `~` needed a judge
