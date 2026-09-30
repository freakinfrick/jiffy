import json

import pytest

from jiffy.bench import bench
from jiffy.bench.grade import BLANK_INSTRUCTIONS, FIT_INSTRUCTIONS, grade_line, unfilled
from jiffy.bench.harness import BenchRefused
from jiffy.judge.backends import Stub


class Fixed(Stub):
    """noul returns `p` for the fit question and `stated` for blank questions;
    raises for lines containing `boom`."""

    def __init__(self, p, stated=0.1):
        super().__init__(seed=0)
        self.p, self.stated, self.seen = p, stated, []

    def noul(self, state, instructions, yes=None, no=None):
        self.seen.append((state, instructions))
        if "boom" in state.get("line", ""):
            raise RuntimeError("grader down")
        return self.stated if "blank" in state else self.p


def run(docs, cases, cfg, factory, **kw):
    kw.setdefault("all_", True)
    return bench(cases_path=cases, cfg=cfg, docs=docs, judge_factory=factory, save=False, **kw)


def test_unfilled_counts_placeholders():
    assert unfilled("fd -e {{txt}} {{path/to/dir}}") == 2
    assert unfilled("ls ~/*.py") == 0
    assert unfilled("") == 0


def test_grader_sees_only_request_and_line():
    j = Fixed(0.9)
    assert grade_line(j, "list python files", "ls ~/*.py") == 0.9
    state, instructions = j.seen[0]
    assert state == {"user_request": "list python files", "line": "ls ~/*.py"}
    assert instructions == FIT_INSTRUCTIONS and len(j.seen) == 1     # no blanks, one question


def test_open_blank_passes_stated_blank_fails():
    open_ = Fixed(0.9, stated=0.1)
    assert grade_line(open_, "download a file from a url", "wget {{url}}") == pytest.approx(0.9)
    assert [i for _, i in open_.seen] == [FIT_INSTRUCTIONS, BLANK_INSTRUCTIONS]
    assert open_.seen[1][0]["blank"] == "{{url}}"
    stated = Fixed(0.9, stated=0.8)
    assert grade_line(stated, "show the first ten lines", "head -n {{count}} f") == pytest.approx(0.2)


def test_worst_blank_decides():
    class Mixed(Fixed):
        def noul(self, state, instructions, yes=None, no=None):
            if state.get("blank") == "{{b}}":
                return 0.7
            return super().noul(state, instructions, yes, no)
    assert grade_line(Mixed(0.95, stated=0.0), "q", "x {{a}} {{b}}") == pytest.approx(0.3)


def test_empty_line_is_zero_without_a_call():
    j = Fixed(0.9)
    assert grade_line(j, "anything", "  ") == 0.0 and not j.seen


def test_line_off_by_default(docs, cases_file, cfg, stub_factory):
    res = run(docs, cases_file, cfg, stub_factory)
    assert res["line"] is None
    assert all("line_ok" not in r for r in res["cases"])
    assert all("line" in r and "unfilled" in r for r in res["cases"])   # deterministic parts always


def test_line_grades_every_case_and_splits(docs, tmp_path, cfg):
    cases = [{"q": "list files in a folder", "cmd": "alpha", "type": "args", "split": "dev"},
             {"q": "search text in files", "cmd": "beta", "type": "values", "split": "heldout"},
             {"q": "list files folder variant7", "cmd": "decoy7"}]          # no tags: dev, untyped
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cases))
    res = run(docs, p, cfg, lambda name: Fixed(0.8), line=True)
    ln = res["line"]
    assert ln["graded"] == 3 and ln["ok_n"] == 3 and ln["ok_rate"] == 1.0
    assert ln["unfilled_rate"] == sum(r["unfilled"] > 0 for r in res["cases"]) / 3
    assert ln["by_split"]["dev"]["n"] == 2 and ln["by_split"]["heldout"]["n"] == 1
    assert set(ln["by_type"]) == {"args", "values"}


def test_below_threshold_is_not_ok(docs, cases_file, cfg):
    res = run(docs, cases_file, cfg, lambda name: Fixed(0.2), line=True)
    assert res["line"]["ok_n"] == 0 and res["line"]["graded"] == 3


def test_failed_grade_is_excluded_not_passed(docs, cfg, tmp_path):
    from jiffy.types import Doc, Example
    d = {}                                  # one command: the stub judge cannot pick another
    d["alpha"] = Doc("alpha", "common", "list files in a folder",
                     [Example("list files in a folder", "alpha boom")], "tldr")
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"q": "list files in a folder", "cmd": "alpha"}]))
    res = run(d, p, cfg, lambda name: Fixed(0.9), line=True)
    row = res["cases"][0]
    assert row["line"] == "alpha boom"
    assert row["line_p"] is None and row["line_ok"] is False
    assert res["line"]["failed"] == 1 and res["line"]["graded"] == 0


def test_split_filter_and_multiple_case_files(docs, cases_file, cfg, stub_factory, tmp_path):
    p = tmp_path / "more.json"
    p.write_text(json.dumps([{"q": "search text in files", "cmd": "beta", "split": "heldout"}]))
    assert run(docs, [cases_file, p], cfg, stub_factory)["n_cases"] == 4
    assert run(docs, [cases_file, p], cfg, stub_factory, split="heldout")["n_cases"] == 1


def test_line_with_a_free_judge_needs_all(docs, tmp_path, cfg, stub_factory):
    p = tmp_path / "many.json"
    p.write_text(json.dumps([{"q": f"list files {i}", "cmd": "alpha"} for i in range(30)]))
    with pytest.raises(BenchRefused):
        run(docs, p, cfg, stub_factory, line=True, all_=False, limit=30)
    run(docs, p, cfg, stub_factory, line=False, all_=False, limit=30)      # free without --line


def test_refusal_counts_what_the_ledger_already_spent(docs, cases_file, cfg, stub_factory, tmp_path):
    import jiffy.judge._http as http
    http.Ledger().add("jev", 0, cfg.usd_cap - 1e-6)          # all but nothing left
    with pytest.raises(BenchRefused, match="already spent"):
        run(docs, cases_file, cfg, stub_factory, line=True)


def test_labels_skip_unsure_and_a_missing_file_is_none(tmp_path):
    from jiffy.bench.grade import load_labels
    p = tmp_path / "lines.jsonl"
    p.write_text('{"q": "a", "line": "x", "label": 1}\n{"q": "a", "line": "y", "label": "unsure"}\n'
                 '{"q": "b", "line": "z", "label": 0}\n')
    assert load_labels(p) == {("a", "x"): 1, ("b", "z"): 0}
    assert load_labels(tmp_path / "nope.jsonl") == {} and load_labels(None) == {}


def test_a_labelled_line_is_graded_by_its_label_without_a_call(docs, cases_file, cfg, tmp_path):
    first = run(docs, cases_file, cfg, lambda name: Fixed(0.9), line=True, labels=None)
    r0 = first["cases"][0]
    assert r0["graded_by"] == "judge"
    p = tmp_path / "lines.jsonl"
    p.write_text(json.dumps({"q": r0["q"], "line": r0["line"], "label": 0}) + "\n")
    graders = []

    def factory(name):
        graders.append(Fixed(0.9))
        return graders[-1]
    res = run(docs, cases_file, cfg, factory, line=True, labels=p)
    row = next(r for r in res["cases"] if r["q"] == r0["q"])
    assert row["graded_by"] == "label" and row["line_p"] == 0.0 and not row["line_ok"]
    assert res["line"]["by_label"] == 1
    assert all(s.get("line") != r0["line"] or s.get("user_request") != r0["q"]
               for g in graders for s, _ in g.seen)          # the judge never saw it
