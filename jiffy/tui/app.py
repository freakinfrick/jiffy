"""The prompt itself. Spotlight, not an application.

prompt_toolkit's own machinery does the work: a `PromptSession`, an
`AutoSuggest` for the ghost text, a `KeyBindings` registry, a `Validator`. No
full-screen layer, so readline semantics, history search and bracketed paste are
whatever the user already knows.

The one rule the whole file is arranged around: nothing in here runs a command.
`shell.execute` does, once, after `jiffy.gate` has been asked.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import statistics
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.validation import Validator

from jiffy import gate
from jiffy.config import CONFIG_DIR, Config, load as load_config
from jiffy.corpus import options
from jiffy.corpus.gloss import gloss
from jiffy.types import GateVerdict, Tier

from . import keys, render, shell
from .engine import Engine
from .lexer import PlaceholderLexer
from .placeholders import first_span, has_placeholder, next_span, span_at

__all__ = ["Tui", "run"]

PROMPT = "» "      # »


def _mount_below(session: PromptSession, content) -> None:
    """Put `content` in the prompt's own layout, directly under the input line.

    Measured in a real terminal before settling on this. `bottom_toolbar` pins
    to the BOTTOM of the screen once the terminal's height is known, leaving a
    screenful of dead rows between the input and the block. The native
    completion menu does render adjacent — but it writes the highlighted
    candidate into the buffer as you arrow through it, which would replace your
    request mid-scroll and restart the search.

    So this takes the menu's position without its semantics: a plain window
    inserted after the input, shown only while the line is live. The input
    window itself reports an unbounded height and absorbs every spare row, which
    is what pushed a toolbar to the bottom; pinning it to its content is what
    lets the block sit flush beneath it.
    """
    from prompt_toolkit.filters import Condition, is_done, to_filter
    from prompt_toolkit.layout.containers import ConditionalContainer, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    block = ConditionalContainer(
        Window(FormattedTextControl(content), dont_extend_height=True, wrap_lines=False),
        filter=~is_done & Condition(lambda: bool(content())),
    )
    session.app.layout.container.children.insert(1, block)
    session.app.layout.current_window.dont_extend_height = to_filter(True)


def _indent(row):
    """One left margin, applied in one place."""
    return FormattedText([("", render.MARGIN), *row]) if row else row


@dataclass(frozen=True)
class PendingRun:
    line: str
    verdict: GateVerdict
    confirmed: bool


class _Ghost(AutoSuggest):
    """Inline preview of the top candidate. Never blocks: it reads whatever the
    engine has finished, and shows nothing while a run is in flight."""

    def __init__(self, ui: "Tui") -> None:
        self.ui = ui

    def get_suggestion(self, buffer, document) -> Suggestion | None:
        if self.ui.accepted or not document.text.strip():
            return None
        opt = self.ui.engine.current()
        if opt is None or opt.empty:
            return None
        return Suggestion(render.ghost(opt.assembled.line))


class Tui:
    """One prompt, one engine, one gate. `run()` loops until EOF."""

    def __init__(self, cfg: Config | None = None, explain: bool = False,
                 answer_fn=None, execute_fn=None) -> None:
        self.cfg = cfg or load_config()
        self.explain = explain
        self.execute = execute_fn or shell.execute

        self.accepted = False          # the buffer holds a command, not a request
        self.confirmed = False         # a CONFIRM line has had its first ENTER
        self.message = ""
        self._pending_since: float | None = None   # start of the in-flight run
        self._durations: list[float] = []
        self.pending_run: PendingRun | None = None

        self.engine = Engine(answer_fn=answer_fn, on_update=self._invalidate).start()
        self._specs: dict = {}         # option tables for the gloss; filled off-thread
        threading.Thread(target=self._load_specs, daemon=True).start()
        self.session = self._session()

    def _load_specs(self) -> None:
        """options.json takes ~0.5 s to read: never on the keystroke path."""
        try:
            self._specs = options.load(self.cfg.corpus_dir)
        except (OSError, ValueError, KeyError):
            return                     # no tables, no gloss; the prompt works without it
        self._invalidate()

    # ------------------------------------------------------------ wiring

    def _history(self):
        """Persistence is OFF by default: in-memory unless the config says otherwise."""
        if self.cfg.persistence.history:
            return FileHistory(str(CONFIG_DIR / "history"))
        return InMemoryHistory()

    def _session(self) -> PromptSession:
        session = PromptSession(
            message=self._prompt,
            rprompt=self._rprompt,
            reserve_space_for_menu=0,
            auto_suggest=_Ghost(self),
            lexer=PlaceholderLexer(self._assembled),
            key_bindings=keys.bindings(self),
            validator=Validator.from_callable(self._valid, error_message="", move_cursor_to_end=False),
            validate_while_typing=False,
            history=self._history(),
            style=render.STYLE,
            refresh_interval=0.2,
            complete_while_typing=False,
            enable_history_search=True,
            multiline=False,
        )
        session.default_buffer.on_text_changed += self._on_text_changed
        _mount_below(session, self._below_prompt)
        return session

    def _assembled(self):
        """What the engine last produced for the line in the buffer, or None."""
        opt = self.engine.current()
        return None if opt is None or opt.empty else opt.assembled

    def _invalidate(self) -> None:
        """The engine finished: refresh the ghost text and repaint.

        prompt_toolkit only recomputes an `AutoSuggest` when the *buffer* changes,
        and our answer lands long after the last keystroke — so the suggestion is
        recomputed here, through the same `AutoSuggest`, and the frame redrawn.
        """
        try:
            app = get_app()
        except Exception:               # no app running (tests, or between turns)
            return
        try:
            buf = app.current_buffer
            buf.suggestion = (None if self.accepted
                              else self.session.auto_suggest.get_suggestion(buf, buf.document))
        except Exception:
            pass
        app.invalidate()

    # ------------------------------------------------------------ state

    def _on_text_changed(self, _buf) -> None:
        self.message = ""
        if self.accepted:
            self.confirmed = False      # the line changed; an old confirmation is void
            return
        self.confirmed = False
        self.engine.request(self.session.default_buffer.text)

    def reset(self) -> None:
        self.accepted = False
        self.confirmed = False
        self.message = ""
        self.pending_run = None

    def verdict_for(self, text: str) -> GateVerdict | None:
        """The gate's reading of exactly what is in the buffer.

        The graph's verdict (which a judge may have raised) is kept while the
        buffer still holds the line it judged; once the user edits it, the
        deterministic pattern gate re-classifies from scratch.
        """
        if not text.strip():
            return None
        opt = self.engine.current()
        if opt is not None and not opt.empty and text.strip() == opt.line.strip():
            return opt.verdict
        return gate.evaluate(text)

    # ------------------------------------------------------------ rendering

    def _typical(self) -> float:
        """This session's own median once we have one, else the benched p50."""
        if len(self._durations) >= 3:
            return statistics.median(self._durations)
        return render.TYPICAL_S

    def _prompt(self):
        """The line you type on. Nothing above it, ever, so it does not move."""
        return FormattedText([("class:jiffy.prompt", PROMPT)])

    def _rprompt(self):
        """The footer, right-aligned on the input line: metadata sits beside the
        thing it describes, and costs no row of its own.

        Hidden once the line is done, so the scrollback keeps a clean record of
        each command you ran rather than a column of stale timings.
        """
        try:
            if get_app().is_done:
                return FormattedText([])
        except Exception:
            pass
        row = self._footer_row()
        return FormattedText([*row, ("", " ")]) if row else row

    def _below_prompt(self):
        """Everything jiffy has to say, rendered directly UNDER the line you type.

        Two properties, and both are the point:

        * the line you are typing never moves. A block above the input pushes it
          down the screen as candidates arrive, which is the jumpiness that made
          this prompt feel unsteady;
        * the block is filtered on `~is_done`, so ENTER erases it before the
          shell writes a byte. The command's output begins on the line under
          your command, and nothing jiffy drew is left sitting in the scrollback
          between one command and the next.

        The footer is not in here: it rides on the input line as the rprompt.
        """
        groups = self._groups()
        if not groups:
            return FormattedText([])
        out: list[tuple[str, str]] = []
        for i, group in enumerate(groups):
            if i:
                out.append(("", "\n"))
            for row in group:
                out.extend(row)
                out.append(("", "\n"))
        while out and out[-1] == ("", "\n"):        # no trailing blank: the block ends where it ends
            out.pop()
        return FormattedText(out)

    def _groups(self) -> list[list]:
        """The block's groups, in order, each already non-empty."""
        return [g for g in ([r for r in self._status_rows() if r], self._gloss_rows()) if g]

    def _gloss_rows(self):
        """What each flag of the line on offer does: the teaching half of jiffy."""
        if self.accepted:
            line = self.session.default_buffer.text if self.session else ""
        else:
            turn = self.engine.turn
            opt = turn.current if turn is not None else None
            line = opt.assembled.line if opt is not None and not opt.empty else ""
        if not line or not self._specs:
            return []
        return render.gloss(gloss(line, self._specs), shutil.get_terminal_size().columns)

    def _status_rows(self):
        text = self.session.default_buffer.text if self.session else ""
        turn = self.engine.turn
        rows: list[FormattedText] = []
        if self.accepted:
            rows.append(_indent(render.banner(text, self.verdict_for(text), self.confirmed)))
        elif turn is not None and turn.current is not None and not turn.current.empty:
            opt = turn.current                     # the gate's reading of the ghost line
            rows.append(_indent(render.degraded_note(turn.answer)))
            rows.append(_indent(render.not_installed_note(opt.name)))
            rows.append(_indent(render.banner(opt.assembled.line, opt.verdict)))
            rows.extend(render.candidates(turn.options, turn.index))
        if self.message:
            rows.append(_indent(FormattedText([("class:jiffy.note", self.message)])))
        # `running`, not `pending`: only an in-flight run earns the horizon bar;
        # typing owes nothing and must not read as a hang.
        if self.engine.running:
            elapsed = self.engine.elapsed
            self._pending_since = time.monotonic() - elapsed
            rows.append(_indent(render.pending_line(elapsed, self._typical())))
        elif self._pending_since is not None:
            self._durations.append(time.monotonic() - self._pending_since)
            self._pending_since = None
        return [r for r in rows if r]

    def _footer_row(self):
        turn = self.engine.turn
        return render.footer(
            turn.answer if turn else None,
            pending=False, usd_cap=self.cfg.usd_cap,
            error=self.engine.error,
            n=len(turn.options) if turn else 0, i=turn.index if turn else 0,
        )

    # ------------------------------------------------------------ validator

    def _valid(self, text: str) -> bool:
        """Third and last refusal: a template or a denied line never leaves the prompt."""
        if not text.strip():
            return False
        if has_placeholder(text):
            return False
        v = self.verdict_for(text)
        return not (v and v.tier >= Tier.DENY)

    # ------------------------------------------------------------ handlers

    def _park(self, buf, span) -> None:
        buf.cursor_position = span[0]

    def _accept_into_buffer(self, buf) -> bool:
        """TAB's work: the autofill becomes the line. Returns False when there is none."""
        opt = self.engine.current()
        if opt is None or opt.empty:
            if self.engine.running or self.engine.pending:
                self.message = "… thinking"
            elif self.engine.turn is None:
                self.message = "press Enter to search"
            else:
                self.message = "nothing matched"
            return False
        self.accepted = True          # set BEFORE the buffer changes: the text-changed
        self.confirmed = False        # hook must not read this as a new query to answer
        buf.document = Document(opt.line, len(opt.line))
        self._record_accepted(opt)
        span = first_span(opt.line)
        if span:
            self._park(buf, span)
            self.message = "type to fill — TAB walks to the next one"
        return True

    def on_tab(self, event) -> None:
        """Accept the autofill. Never runs. With placeholders, walk them."""
        buf = event.current_buffer
        if self.accepted and has_placeholder(buf.text):
            span = next_span(buf.text, buf.cursor_position)
            if span:
                self._park(buf, span)
            return
        if self.accepted:
            return
        self._accept_into_buffer(buf)

    def on_enter(self, event) -> None:
        """Trigger the search; once an answer is ready, run the assembled line."""
        buf = event.current_buffer
        if not buf.text.strip():
            return
        if not self.accepted and not self.engine.answered(buf.text):
            self.engine.run(buf.text)
            return
        if not self.accepted and not self._accept_into_buffer(buf):
            return
        text = buf.text
        if has_placeholder(text):
            span = first_span(text)
            if span:
                self._park(buf, span)
            self.message = "fill the {{placeholders}} first"
            return
        verdict = self.verdict_for(text)
        if verdict is None:
            return
        if verdict.tier >= Tier.DENY:
            self.message = ""           # the banner already says it, struck through
            self.confirmed = False
            return                      # no branch from here reaches shell.execute
        if verdict.tier == Tier.CONFIRM and not self.confirmed:
            self.confirmed = True
            return
        self.pending_run = PendingRun(text, verdict, self.confirmed)
        buf.validate_and_handle()

    def on_next(self, event) -> None:
        self._cycle(event, +1)

    def on_prev(self, event) -> None:
        self._cycle(event, -1)

    def _cycle(self, event, delta: int) -> None:
        """UP/DOWN. Before TAB this only moves the highlight (and with it the
        ghost); after TAB the buffer already holds a command, so swap it."""
        opt = self.engine.cycle(delta)
        if opt is None:
            return
        if self.accepted:               # already a command line: swap it for the alternate
            buf = event.current_buffer
            buf.document = Document(opt.line, len(opt.line))
            self.confirmed = False
            span = first_span(opt.line)
            if span:
                self._park(buf, span)

    def on_char(self, event) -> None:
        """A printable key while the cursor is on a `{{token}}`: the token IS the field.

        Typing replaces the whole token, braces and all, so filling one is just
        typing the value — no deleting first. Anywhere else this is ordinary
        self-insert, which is why the binding is filtered rather than global.
        """
        buf = event.current_buffer
        span = span_at(buf.text, buf.cursor_position)
        if span is None:
            buf.insert_text(event.data)
            return
        start, end = span
        text = buf.text[:start] + event.data + buf.text[end:]
        buf.document = Document(text, start + len(event.data))
        self.confirmed = False                  # the line changed; an old confirmation is void
        if not has_placeholder(text):
            self.message = ""

    def on_escape(self, event) -> None:
        """Back to empty. Also the way out of a CONFIRM."""
        buf = event.current_buffer
        buf.document = Document("", 0)
        self.reset()
        self.engine.clear()

    # ------------------------------------------------------------ persistence

    def _record_accepted(self, opt) -> None:
        """Off by default (spec: "Persistence — everything off by default")."""
        if not self.cfg.persistence.accepted:
            return
        path = Path(CONFIG_DIR) / "accepted.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        turn = self.engine.turn
        with path.open("a") as fh:
            fh.write(json.dumps({"query": turn.query if turn else "", "name": opt.name,
                                 "line": opt.assembled.line}) + "\n")

    # ------------------------------------------------------------ loop

    def _settle(self, stream=None) -> None:
        """Rescue a last line of output that did not end in a newline.

        Called after the shell has had the terminal and before the next prompt
        draws over whatever it left behind. A closed or redirected stdout is not
        a terminal and needs none of this.
        """
        stream = stream or sys.stdout
        try:
            if not stream.isatty():
                return
            stream.write(render.settle(shutil.get_terminal_size().columns))
            stream.flush()
        except (OSError, ValueError):        # detached or closed stdout
            pass

    def run(self) -> int:
        """Type, accept, run. Returns the last exit code."""
        code = 0
        while True:
            self.reset()
            self.engine.clear()          # a fresh prompt owes nothing to the last request
            try:
                self.session.prompt()
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            pending, self.pending_run = self.pending_run, None
            if pending is None:
                continue
            code = self._execute(pending)
        return code

    def _execute(self, pending: PendingRun) -> int:
        res = self.execute(pending.line, pending.verdict, pending.confirmed)
        self._settle()
        if isinstance(res, shell.Refusal):
            print(res.reason)
            return 1
        turn = self.engine.turn
        if self.explain and turn is not None:
            print(turn.answer.trace.render())
        return res.returncode


def enter_workdir(workdir: str) -> str | None:
    """Move the process to where commands should run. Returns a note on failure.

    One `chdir`, at startup, instead of threading a directory through every
    caller: the shell inherits it, and the fill sources — which read
    `Path.cwd()` to resolve "this directory" and to offer real filenames — see
    the same place the command will run in. Two notions of "here" would let a
    filled path point somewhere the command never looks.
    """
    if workdir.strip() in ("", "."):
        return None
    target = os.path.expanduser(os.path.expandvars(workdir))
    try:
        os.chdir(target)
    except OSError as exc:
        return f"workdir {workdir!r} unusable ({exc.strerror}); staying in {os.getcwd()}"
    return None


def run(cfg: Config | None = None, explain: bool = False) -> int:
    cfg = cfg or load_config()
    note = enter_workdir(cfg.workdir)
    if note:
        print(note, file=sys.stderr)
    ui = Tui(cfg=cfg, explain=explain)
    try:
        return ui.run()
    finally:
        ui.engine.stop()
