"""The candidate list as rows: what is shown, what is marked, what is hidden."""
from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.formatted_text import to_plain_text

from jiffy.tui import render
from jiffy.types import GateVerdict, Tier


@dataclass(frozen=True)
class _Asm:
    line: str


@dataclass(frozen=True)
class _Opt:
    """The shape `render.candidates` reads off an `engine.Option`."""

    assembled: _Asm
    verdict: GateVerdict | None = None

    @property
    def empty(self) -> bool:
        return not self.assembled.line


def _opts(n: int, verdict=None) -> list[_Opt]:
    return [_Opt(_Asm(f"cmd-{i}"), verdict) for i in range(n)]


def _plain(rows) -> list[str]:
    return [to_plain_text(r) for r in rows]


def _body(row: str) -> str:
    """A row without its margin, rail and selection caret."""
    return row.replace(render.RAIL, "").replace(render.SEL_MARK.strip(), "").strip()


def test_a_lone_candidate_is_not_a_list():
    """One option is the ghost line; a list of one is noise."""
    assert render.candidates(_opts(1)) == []
    assert render.candidates([]) == []


def test_every_candidate_is_a_row_and_the_selection_is_marked():
    rows = _plain(render.candidates(_opts(3), index=1))
    assert len(rows) == 3
    assert all(r.startswith(render.MARGIN + render.RAIL) for r in rows)   # one rail, one margin
    assert [_body(r) for r in rows] == ["cmd-0", "cmd-1", "cmd-2"]
    assert render.SEL_MARK.strip() in rows[1]
    assert render.SEL_MARK.strip() not in rows[0]


def test_empty_options_are_dropped():
    opts = _opts(2) + [_Opt(_Asm(""))]
    assert len(render.candidates(opts)) == 2


def test_a_long_list_windows_around_the_selection_and_names_what_is_hidden():
    rows = _plain(render.candidates(_opts(12), index=9, limit=4))
    assert len(rows) == 5                       # 4 candidates + the "+N more" line
    assert "cmd-9" in " ".join(rows)            # the selection is always in frame
    assert _body(rows[-1]) == "+8 more"


def test_the_selection_stays_in_frame_at_both_ends():
    first = _plain(render.candidates(_opts(12), index=0, limit=4))
    last = _plain(render.candidates(_opts(12), index=11, limit=4))
    assert "cmd-0" in first[0]
    assert "cmd-11" in last[3]


def test_a_denied_candidate_is_styled_as_denied():
    """ENTER will refuse it, so it must not read like a line you can pick."""
    denied = GateVerdict(Tier.DENY, "wipes the disk", "rm-rf-root")
    rows = render.candidates(_opts(2, denied), index=0)
    classes = [frag[0] for row in rows for frag in row]
    assert "class:jiffy.cand.deny" in classes
    assert "class:jiffy.cand.sel" not in classes


# ---------------------------------------------------------------- fragments


def _fill(token, value, source, start, end):
    from jiffy.types import Fill
    return Fill(token, value, source, "", start, end)


def test_an_unfilled_token_is_loud_and_its_braces_are_not():
    line = "find {{path/to/directory}} -name '*.log'"
    frags = render.fragments(line, [_fill("path/to/directory", "", "", 5, 26)])
    styles = dict((t, s) for s, t in frags)
    assert styles["{{"] == "class:jiffy.ph.brace"
    assert styles["path/to/directory"] == "class:jiffy.ph.unresolved"
    assert "".join(t for _s, t in frags) == line          # never alters the text


def test_a_value_from_the_request_is_drawn_like_ordinary_text():
    """You typed it; flagging it back at you is noise."""
    line = "du -sh ~"
    frags = render.fragments(line, [_fill("path/to/directory", "~", "query", 7, 8)], base="")
    assert all(style == "" for style, _t in frags)


def test_a_value_inferred_from_the_box_is_greyed():
    line = "du -sh /home/user"
    frags = render.fragments(line, [_fill("path/to/directory", "/home/user", "env", 7, 17)])
    assert ("class:jiffy.ph.inferred", "/home/user") in frags


def test_fragments_survive_nonsense_spans():
    """Spans describe the line the graph built; a stale one must not crash the prompt."""
    line = "ls -la"
    assert "".join(t for _s, t in render.fragments(line, [_fill("x", "y", "query", 99, 120)])) == line


def test_the_footer_says_nothing_before_anything_has_been_asked():
    """An empty prompt is one line tall."""
    assert render.footer(None) == []
    assert render.footer(None, error="boom") != []


def _traced(*nodes):
    from types import SimpleNamespace
    from jiffy.types import NodeTrace
    return SimpleNamespace(trace=SimpleNamespace(nodes=[
        NodeTrace(nid, "pick", "jev", 1, 1, 1.0, 0.0, deg, detail) for nid, deg, detail in nodes]))


def test_budget_degradation_gets_its_own_row():
    """The rprompt vanishes in a narrow pane; `top` for a tree query must not look judged."""
    ans = _traced(("retrieve", False, ""), ("pick", True, "error=BudgetExceeded: jiffy daily usd_cap"))
    text = to_plain_text(render.degraded_note(ans))
    assert "budget" in text and "tomorrow" in text


def test_other_degradation_names_the_nodes():
    ans = _traced(("pick", True, "error=JudgeUnavailable"), ("critic", True, "error=timeout"))
    assert "pick, critic" in to_plain_text(render.degraded_note(ans))


def test_no_note_when_nothing_degraded():
    assert to_plain_text(render.degraded_note(_traced(("pick", False, "")))) == ""
    assert to_plain_text(render.degraded_note(None)) == ""


def test_gloss_rows_align_flags_and_clip_to_the_pane():
    rows = _plain(render.gloss([("-L 2", "Max display depth"), ("-a", "x" * 200)], columns=40))
    assert rows[0] == render.MARGIN + "-L 2  Max display depth"
    assert rows[1].startswith(render.MARGIN + "-a    x") and len(rows[1]) <= 40 and rows[1].endswith("…")
    assert render.gloss([]) == []


def test_an_overlong_flag_does_not_widen_the_column_for_the_rest():
    rows = _plain(render.gloss([("-h", "human"), ("--format=" + "w" * 20, "fmt")], columns=80))
    assert rows[0] == render.MARGIN + "-h  human"
    assert rows[1] == render.MARGIN + "--format=" + "w" * 20 + "  fmt"


def test_a_missing_program_is_named_and_an_installed_one_is_not():
    assert to_plain_text(render.not_installed_note("jiffy-surely-absent-xyz")) == \
        "jiffy-surely-absent-xyz is not installed here"
    assert to_plain_text(render.not_installed_note("ls -la")) == ""
    assert to_plain_text(render.not_installed_note("")) == ""
