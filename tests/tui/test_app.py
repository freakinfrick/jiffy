"""The interaction contract, driven by hand against the stub judge."""
from __future__ import annotations

import os
import time

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from jiffy import gate
from jiffy.graph.assemble import PLACEHOLDER
from jiffy.tui import render
from jiffy.tui.app import Tui
from jiffy.tui.engine import Turn, options_for
from jiffy.tui.placeholders import has_placeholder
from jiffy.types import Tier

QUERY = "find files bigger than a size"


def _ui(cfg, answer_fn, query=QUERY):
    ui = Tui(cfg=cfg, answer_fn=answer_fn)
    ui.engine.stop()
    ans = answer_fn(query)
    ui.engine.turn = Turn(query, ans, options_for(ans, query))
    return ui


def _buf(text):
    b = Buffer()
    b.document = Document(text, len(text))
    return b


def test_tab_fills_the_buffer_and_does_not_run(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    buf = _buf(QUERY)
    ui.on_tab(event(buf))
    assert buf.text == ui.engine.current().line
    assert buf.text.endswith(f"  # {QUERY}")
    assert ui.accepted is True
    assert ui.pending_run is None            # TAB never schedules a run


def test_ghost_text_previews_the_top_candidate(app_session, cfg, answer_fn):
    ui = _ui(cfg, answer_fn)
    suggestion = ui.session.auto_suggest.get_suggestion(_buf(QUERY), Document(QUERY, len(QUERY)))
    assert suggestion is not None
    assert ui.engine.current().assembled.line in suggestion.text
    ui.accepted = True                        # once accepted, no more ghost
    assert ui.session.auto_suggest.get_suggestion(_buf(QUERY), Document(QUERY, len(QUERY))) is None


def test_enter_schedules_the_assembled_line(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    buf = _buf(QUERY)
    ui.on_tab(event(buf))
    if has_placeholder(buf.text):             # a template must be filled first
        filled = PLACEHOLDER.sub("X", buf.text)
        buf.document = Document(filled, len(filled))
    line = buf.text
    ui.on_enter(event(buf))
    if ui.pending_run is None:                # CONFIRM: a second ENTER runs it
        assert ui.confirmed is True
        ui.on_enter(event(buf))
    assert ui.pending_run is not None
    assert ui.pending_run.line == line        # exactly what was on screen


def test_a_line_with_placeholders_is_never_run(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    line = "find {{path}} -name '{{pattern}}'  # find files by name"
    ui.accepted = True
    buf = _buf(line)
    ui.on_enter(event(buf))
    assert ui.pending_run is None
    assert buf.cursor_position == line.index("{{path}}")
    ui.on_tab(event(buf))                     # TAB walks to the next token
    assert buf.cursor_position == line.index("{{pattern}}")
    assert ui._valid(line) is False


def test_confirm_needs_a_second_enter_and_esc_edits(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    line = "chmod -R 777 /srv/app"
    verdict = gate.evaluate(line)
    if verdict.tier != Tier.CONFIRM:
        return
    ui.accepted = True
    buf = _buf(line)
    ui.on_enter(event(buf))
    assert ui.pending_run is None and ui.confirmed is True
    ui.on_enter(event(buf))
    assert ui.pending_run is not None and ui.pending_run.confirmed is True

    ui.pending_run, ui.confirmed = None, True
    ui.on_escape(event(buf))
    assert buf.text == "" and ui.confirmed is False and ui.accepted is False


def test_escape_clears_back_to_empty(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    buf = _buf(QUERY)
    ui.on_tab(event(buf))
    ui.on_escape(event(buf))
    assert buf.text == ""
    assert ui.accepted is False and ui.message == ""


def test_cycling_moves_through_the_alternates(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    turn = ui.engine.turn
    if len(turn.options) < 2:
        return
    first = ui.engine.current().line
    ui.on_next(event(_buf(QUERY)))
    assert ui.engine.current().line != first
    ui.on_prev(event(_buf(QUERY)))
    assert ui.engine.current().line == first


def test_footer_names_judge_and_degradation(app_session, cfg, answer_fn):
    ui = _ui(cfg, answer_fn)
    text = "".join(t for _s, t in ui._rprompt())      # the footer rides on the input line
    ans = ui.engine.turn.answer
    assert render.judge_that_answered(ans) in text
    assert render.SEP.strip() in text                 # the footer reads as one dotted line
    assert ("degraded" in text) == render.degraded(ans)
    assert f"${ans.trace.total_usd:.4f}" in text   # the paid path never looks like the free one


def test_nothing_matched_renders_as_nothing_matched(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn, query="zzzzz no such command zzzzz")
    opt = ui.engine.current()
    if opt is None or not opt.empty:
        return
    buf = _buf("zzzzz no such command zzzzz")
    ui.on_tab(event(buf))
    assert ui.accepted is False
    assert ui.message == "nothing matched"
    ui.on_enter(event(buf))
    assert ui.pending_run is None


def test_persistence_is_off_by_default(app_session, cfg, answer_fn, event, tmp_path):
    assert cfg.persistence.history is False and cfg.persistence.accepted is False
    ui = _ui(cfg, answer_fn)
    buf = _buf(QUERY)
    ui.on_tab(event(buf))
    time.sleep(0.02)
    written = list(tmp_path.rglob("*"))
    assert [p for p in written if p.name in {"history", "accepted.jsonl"}] == []
    assert type(ui.session.history).__name__ == "InMemoryHistory"


def test_accepting_does_not_fire_a_new_graph_run(app_session, cfg, answer_fn, event):
    """TAB rewrites the buffer; that rewrite must not look like a fresh query."""
    ui = _ui(cfg, answer_fn)
    buf = _buf(QUERY)
    ui.session.default_buffer.document = Document(QUERY, len(QUERY))
    ui.engine.request(QUERY)
    before = ui.engine._query
    ui.on_tab(event(buf))
    ui._on_text_changed(buf)                  # what prompt_toolkit fires on the rewrite
    assert ui.engine._query == before         # still the request, not the command line
    assert ui.engine.pending is False


def test_the_candidate_list_renders_above_the_prompt(app_session, cfg, answer_fn):
    """Multiple commands are on screen at once, not one at a time behind a key."""
    ui = _ui(cfg, answer_fn)
    turn = ui.engine.turn
    if len(turn.options) < 2:
        return
    text = "".join(t for _s, t in ui._below_prompt())
    for opt in turn.options[:render.LIST_LIMIT]:
        assert opt.assembled.line in text
    assert render.SEL_MARK.strip() in text


def test_arrows_move_the_highlight_and_the_ghost_together(app_session, cfg, answer_fn, event):
    """Before TAB the arrows only select: the buffer is still the user's request."""
    ui = _ui(cfg, answer_fn)
    if len(ui.engine.turn.options) < 2:
        return
    buf = _buf(QUERY)
    ui.on_next(event(buf))

    assert buf.text == QUERY                       # unaccepted: nothing was written
    selected = ui.engine.current()
    assert ui.engine.turn.index == 1
    suggestion = ui.session.auto_suggest.get_suggestion(buf, Document(QUERY, len(QUERY)))
    assert selected.assembled.line in suggestion.text

    rows = "".join(t for _s, t in ui._below_prompt())
    marked = [ln for ln in rows.splitlines() if render.SEL_MARK.strip() in ln]
    assert marked and selected.assembled.line in marked[0]


def test_arrows_swap_the_line_once_it_is_accepted(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    if len(ui.engine.turn.options) < 2:
        return
    buf = _buf(QUERY)
    ui.on_tab(event(buf))
    ui.on_next(event(buf))
    assert buf.text == ui.engine.current().line
    assert ui.confirmed is False                   # a swapped line is unconfirmed again


def test_no_thinking_horizon_until_a_run_is_in_flight(app_session, cfg, answer_fn):
    """Typing (owed work) must not summon a progress bar; only a run does."""
    ui = _ui(cfg, answer_fn)

    class _Pending:                            # owed, but not started
        pending, running, elapsed, turn, error = True, False, 0.0, ui.engine.turn, ""

        def current(self):
            return self.turn.current

    ui.engine = _Pending()
    assert render.PENDING not in "".join(t for _s, t in ui._below_prompt())

    class _Running(_Pending):
        running, elapsed = True, 2.0

    ui.engine = _Running()
    assert render.PENDING in "".join(t for _s, t in ui._below_prompt())


def test_typing_wipes_the_previous_answer_off_the_screen(app_session, cfg, answer_fn):
    """The list and the ghost belong to the text on screen, or to nothing."""
    ui = _ui(cfg, answer_fn)
    ui.engine.start()
    try:
        doc = Document(QUERY, len(QUERY))
        assert ui.session.auto_suggest.get_suggestion(_buf(QUERY), doc) is not None

        ui.engine.request(QUERY + " somewhere else")     # a keystroke

        after = "".join(t for _s, t in ui._below_prompt())
        assert ui.engine.current() is None
        assert render.SEL_MARK.strip() not in after      # no candidate list
        assert render.PENDING not in after               # and no horizon yet either
        assert ui.session.auto_suggest.get_suggestion(_buf(QUERY), doc) is None
    finally:
        ui.engine.stop()


def test_every_alternate_is_filled_like_the_winner(app_session, cfg, answer_fn):
    """A list where the top line is filled and the rest are templates reads as two systems."""
    from jiffy.tui.engine import options_for
    ans = answer_fn("find files bigger than a size in the home directory")
    opts = options_for(ans, "find files bigger than a size in the home directory")
    filled = [o for o in opts if o.assembled.fills]
    assert filled, "no option recorded a fill"
    assert all(o.assembled.fills or "{{" not in o.assembled.line for o in opts)


def test_the_prompt_is_one_line_when_nothing_has_been_asked(app_session, cfg, answer_fn):
    """Whitespace is intentional: an idle prompt draws no gaps around nothing."""
    ui = Tui(cfg=cfg, answer_fn=answer_fn)
    ui.engine.stop()
    ui.engine.turn = None
    assert ui._below_prompt() == []                      # nothing under the line
    assert "".join(t for _s, t in ui._prompt()) == "» "


def test_groups_are_separated_by_exactly_one_blank_line(app_session, cfg, answer_fn):
    ui = _ui(cfg, answer_fn)
    text = "".join(t for _s, t in ui._below_prompt())
    assert "\n\n\n" not in text                       # never two gaps in a row
    assert not text.endswith("\n")                      # and none left dangling at the end


def test_nothing_jiffy_draws_sits_above_the_line_you_type(app_session, cfg, answer_fn):
    """The input line must not move as candidates arrive, so the block goes below."""
    ui = _ui(cfg, answer_fn)
    assert "".join(t for _s, t in ui._prompt()) == "» "   # the prompt is only the prompt
    below = "".join(t for _s, t in ui._below_prompt())
    right = "".join(t for _s, t in ui._rprompt())
    assert render.SEP.strip() in right                  # metadata beside the line
    assert render.SEP.strip() not in below              # and not duplicated under it


def test_the_block_is_mounted_directly_under_the_input(app_session, cfg, answer_fn):
    """Position is structural: right after the input in the layout, and the input
    no longer absorbs spare rows (which is what pinned a toolbar to the bottom)."""
    ui = _ui(cfg, answer_fn)
    layout = ui.session.app.layout
    assert layout.current_window.dont_extend_height()
    mounted = layout.container.children[1]
    assert "ConditionalContainer" in type(mounted).__name__


def test_the_footer_is_hidden_on_a_finished_line(app_session, cfg, answer_fn, monkeypatch):
    """Scrollback keeps the command you ran, not a column of stale timings."""
    ui = _ui(cfg, answer_fn)

    class _Done:
        is_done = True

    monkeypatch.setattr("jiffy.tui.app.get_app", lambda: _Done())
    assert ui._rprompt() == []


def test_typing_on_a_placeholder_replaces_the_whole_token(app_session, cfg, answer_fn, event):
    """The token is the field: you type the value, you do not delete braces first."""
    ui = _ui(cfg, answer_fn)
    buf = _buf("tree -L {{num}}")
    buf.cursor_position = buf.text.index("{{")
    for ch in "2":
        ev = event(buf)
        ev.data = ch
        ui.on_char(ev)
    assert buf.text == "tree -L 2"
    assert buf.cursor_position == len("tree -L 2")
    assert not has_placeholder(buf.text)


def test_typing_a_multi_character_value_into_a_token(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    buf = _buf("cp {{source}} {{target}}")
    buf.cursor_position = buf.text.index("{{")
    for ch in "a.txt":
        ev = event(buf)
        ev.data = ch
        ui.on_char(ev)
    assert buf.text == "cp a.txt {{target}}"        # only the token under the cursor


def test_typing_off_a_placeholder_is_ordinary_self_insert(app_session, cfg, answer_fn, event):
    ui = _ui(cfg, answer_fn)
    buf = _buf("ls -la")
    buf.cursor_position = len(buf.text)
    ev = event(buf)
    ev.data = "x"
    ui.on_char(ev)
    assert buf.text == "ls -lax"


def test_filling_the_last_token_unblocks_enter(app_session, cfg, answer_fn, event):
    """The three refusals are about templates; a filled line is not one."""
    ui = _ui(cfg, answer_fn)
    buf = _buf("tree -L {{num}}")
    buf.cursor_position = buf.text.index("{{")
    ev = event(buf)
    ev.data = "2"
    ui.on_char(ev)
    ui.accepted = True
    assert ui._valid(buf.text) is True


def test_tab_then_type_fills_the_next_token(app_session, cfg, answer_fn, event):
    """The walk and the fill have to compose: TAB moves, typing replaces."""
    ui = _ui(cfg, answer_fn)
    line = "aws s3 cp {{path/to/file}} s3://{{bucket}}/{{remote}}"
    buf = _buf(line)
    ui.accepted = True
    buf.cursor_position = line.index("{{")

    def type_in(word):
        for ch in word:
            ev = event(buf)
            ev.data = ch
            ui.on_char(ev)

    type_in("notes.txt")
    assert buf.text == "aws s3 cp notes.txt s3://{{bucket}}/{{remote}}"

    ui.on_tab(event(buf))                      # walk to {{bucket}}
    assert buf.cursor_position == buf.text.index("{{bucket}}")
    type_in("my-bucket")
    assert buf.text == "aws s3 cp notes.txt s3://my-bucket/{{remote}}"

    ui.on_tab(event(buf))                      # walk to the last one
    type_in("archive/notes.txt")
    assert buf.text == "aws s3 cp notes.txt s3://my-bucket/archive/notes.txt"
    assert not has_placeholder(buf.text)


def test_commands_run_from_home_by_default(tmp_path, monkeypatch):
    """`tree -L 2` from ~/jiffy printed the repo, not your home. The default is ~."""
    from jiffy.config import Config
    from jiffy.tui.app import enter_workdir
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir("/")
    assert Config().workdir == "~"
    assert enter_workdir("~") is None
    assert os.getcwd() == str(tmp_path)


def test_dot_keeps_the_launch_directory(tmp_path, monkeypatch):
    from jiffy.tui.app import enter_workdir
    monkeypatch.chdir(tmp_path)
    assert enter_workdir(".") is None
    assert os.getcwd() == str(tmp_path)


def test_a_bad_workdir_is_reported_not_fatal(tmp_path, monkeypatch):
    from jiffy.tui.app import enter_workdir
    monkeypatch.chdir(tmp_path)
    note = enter_workdir(str(tmp_path / "does-not-exist"))
    assert note and "staying in" in note
    assert os.getcwd() == str(tmp_path)


def test_the_env_can_override_the_workdir(tmp_path, monkeypatch):
    from jiffy.config import load
    monkeypatch.setenv("JIFFY_WORKDIR", "/tmp")
    assert load(tmp_path / "config.toml").workdir == "/tmp"


def test_a_fresh_prompt_shows_nothing_from_the_last_request(app_session, cfg, answer_fn):
    """After a command runs, the next empty prompt must not wear the old candidate list."""
    ui = _ui(cfg, answer_fn)
    assert ui._below_prompt() != []                 # the old answer is on screen

    calls = []

    def fake_prompt():
        calls.append(ui._below_prompt())
        raise EOFError

    ui.session.prompt = fake_prompt
    ui.run()
    assert calls == [[]]                            # the new prompt opened clean
