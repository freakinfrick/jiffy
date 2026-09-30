"""The cancellable pipeline run, triggered by ENTER — never by typing.

A run takes ~1.7s (p50) against the paid judge, so the prompt can never wait
on it. One worker thread owns every run:

* a keystroke calls `request(query)`, which only drops any stale answer and
  bumps a generation counter — it never starts a run, so typing is free;
* ENTER calls `run(query)`, which starts the run immediately: the user decides
  when the line is worth answering;
* a run whose generation is stale is cancelled through a `CancelToken` the
  pipeline polls between stages, so a superseded query stops at the next stage
  boundary instead of paying for the rest. The stage already in flight cannot
  be killed — it finishes against a discarded sandbox — so the waste is
  bounded by one stage, not by the whole run;
* the screen never shows a run nobody asked for: an edit clears the turn, so
  the candidate list and the ghost belong to the text on screen or to nothing.

`pending` and `running` are not the same question. `pending` means a run is
owed but not yet started — the sliver between `run()` and the worker picking it
up. `running` means a pipeline run is actually in flight, and only that one earns
the "… thinking" horizon.

Nothing here touches prompt_toolkit: the UI hands in an `on_update` callback and
polls `current()`. That is what makes it testable without a terminal.
"""
from __future__ import annotations

import inspect
import threading
import time
from dataclasses import dataclass
from typing import Callable

from jiffy import gate
from jiffy.fill import Sources, resolve as fill_resolve
from jiffy.graph.api import answer as _answer, corpus
from jiffy.graph.cancel import Cancelled, CancelToken
from jiffy.types import Answer, Assembled, Candidate, GateVerdict

from .assemble import assemble, sanitise

__all__ = ["Engine", "Option", "Turn", "options_for"]


@dataclass(frozen=True)
class Option:
    """One cycleable candidate, already assembled and already gated."""

    name: str
    assembled: Assembled
    verdict: GateVerdict

    @property
    def line(self) -> str:
        return self.assembled.comment_line

    @property
    def placeholders(self) -> list[str]:
        return list(self.assembled.placeholders)

    @property
    def empty(self) -> bool:
        """The pipeline's sentinel: retrieval matched nothing."""
        return not self.name or not self.assembled.line


@dataclass
class Turn:
    """What one graph run produced, plus which alternate the user is looking at."""

    query: str
    answer: Answer
    options: list[Option]
    index: int = 0

    @property
    def current(self) -> Option | None:
        return self.options[self.index] if self.options else None

    def cycle(self, delta: int) -> Option | None:
        if not self.options:
            return None
        self.index = (self.index + delta) % len(self.options)
        return self.current


def _accepts_cancel(fn: Callable[..., Answer]) -> bool:
    """Does this answer function take `cancelled=`? Test stubs mostly do not."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):                  # builtins, C callables
        return False
    return "cancelled" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _first_line(cand: Candidate) -> str:
    for ex in cand.doc.examples:
        if ex.line.strip():
            return ex.line.strip()
    return cand.name


def options_for(ans: Answer, query: str, max_alternates: int = 8) -> list[Option]:
    """The cycle list: the graph's answer first, then its alternates.

    The winning line is rendered exactly as the graph assembled it — the TUI
    never re-assembles it. An alternate has no assembled form of its own (the
    `invoke` node only ran for the winner), so its first documented example goes
    through the same fill, the same `jiffy.graph.assemble` and the same gate.
    Without that the list would show the winner filled and every alternate full
    of raw tokens, which reads as two different systems.
    """
    sources = Sources.live()
    out: list[Option] = []
    if ans.assembled.line:
        asm = ans.assembled
        if sanitise(asm.request) != asm.request:
            # the graph interpolated a `#` or a control character verbatim; re-wrap
            # the SAME line around a sanitised request rather than ship two comments
            asm = assemble(asm.line, asm.request, asm.fills)
        out.append(Option(ans.candidate.name or asm.line.split()[0], asm, ans.verdict))
    seen = {o.assembled.line for o in out}
    for cand in ans.alternates[:max_alternates]:
        line = _first_line(cand)
        if not line or line in seen:
            continue
        seen.add(line)
        filled = fill_resolve(line, query, sources)
        asm = assemble(filled.line, query, filled.fills)
        out.append(Option(cand.name, asm, gate.evaluate(asm.line)))
    if not out:
        out.append(Option("", ans.assembled, ans.verdict))
    return out


class Engine:
    """Background answering. Start it, feed it keystrokes, read `turn`."""

    def __init__(self, answer_fn: Callable[..., Answer] | None = None,
                 on_update: Callable[[], None] = lambda: None) -> None:
        self._answer = answer_fn or _answer
        self.on_update = on_update
        self.turn: Turn | None = None
        self.error: str = ""
        self._gen = 0
        self._running_gen = -1
        self._started_at: float | None = None      # when the in-flight run began
        self._token: CancelToken | None = None     # the in-flight run's liveness
        self._takes_cancel = _accepts_cancel(self._answer)
        self._query = ""
        self._cv = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> "Engine":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="jiffy-engine", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    # ------------------------------------------------------------ input

    def request(self, query: str) -> None:
        """A keystroke. Cheap: drops any stale answer, cancels any run in flight.

        It does NOT start one — a run starts only when ENTER calls `run()`.
        Editing drops the answer on screen: an answer belongs to the query that
        produced it, and the moment the text changes it is an answer to a
        question nobody asked.
        """
        query = " ".join(query.split())
        with self._cv:
            self._query = query
            if self.turn is not None and self.turn.query == query:
                self._running_gen = self._gen     # this answer is already on screen
                return
            self._gen += 1
            self._running_gen = self._gen         # nothing owed: the run is explicit
            if self._token is not None:           # the run in flight is for older text
                self._token.cancel()
            self._cv.notify_all()
        self.turn, self.error = None, ""
        self.on_update()

    def run(self, query: str) -> None:
        """ENTER: start the run for `query` now. Idempotent for the same text."""
        query = " ".join(query.split())
        with self._cv:
            if self._query == query and (self._gen != self._running_gen
                                         or self._started_at is not None):
                return                       # already owed or in flight for this query
            self._query = query
            self._gen += 1
            if self._token is not None:
                self._token.cancel()
            self._cv.notify_all()
        self.turn, self.error = None, ""
        self.on_update()

    def answered(self, query: str) -> bool:
        """A turn has landed for exactly this text (even the empty sentinel)."""
        query = " ".join(query.split())
        turn = self.turn
        return turn is not None and turn.query == query

    def clear(self) -> None:
        self.request("")

    # ------------------------------------------------------------ output

    @property
    def pending(self) -> bool:
        """A run is owed but not yet started."""
        with self._cv:
            return bool(self._query) and self._gen != self._running_gen

    @property
    def running(self) -> bool:
        """A graph run is in flight right now — not merely owed."""
        with self._cv:
            return self._started_at is not None

    @property
    def elapsed(self) -> float:
        """Seconds the in-flight run has been going; 0 when nothing is running.

        Measured from the run's start, not from the keystroke that asked for it:
        charging pre-run idle to the horizon makes every answer look late.
        """
        with self._cv:
            started = self._started_at
        return time.monotonic() - started if started is not None else 0.0

    def current(self) -> Option | None:
        turn = self.turn
        return turn.current if turn else None

    def cycle(self, delta: int) -> Option | None:
        turn = self.turn
        if turn is None:
            return None
        opt = turn.cycle(delta)
        self.on_update()
        return opt

    # ------------------------------------------------------------ worker

    def _loop(self) -> None:
        if self._answer is _answer:       # the real pipeline: load the index while the user types
            try:
                corpus()
            except Exception:             # noqa: BLE001 - warming is an optimisation; the run reports real errors
                pass
        while True:
            with self._cv:
                while not self._stop and (not self._query or self._gen == self._running_gen):
                    self._cv.wait(0.5)
                if self._stop:
                    return
                gen, query = self._gen, self._query
                self._running_gen = gen
                self._started_at = time.monotonic()
                self._token = token = CancelToken()
            self.on_update()                         # the run has begun: show the horizon
            try:
                self._run_once(gen, query, token)
            finally:
                with self._cv:
                    self._started_at, self._token = None, None

    def _run_once(self, gen: int, query: str, token: CancelToken | None = None) -> None:
        err = ""
        ans: Answer | None = None
        kw = {"cancelled": token} if (token is not None and self._takes_cancel) else {}
        try:
            ans = self._answer(query, **kw)
        except Cancelled:                            # superseded mid-run: not an error
            return
        except Exception as exc:                     # a judge failure is not a UI failure
            err = f"{type(exc).__name__}: {exc}"
        with self._cv:
            if gen != self._gen:                     # superseded while we ran: discard
                return
        self.error = err
        self.turn = Turn(query, ans, options_for(ans, query)) if ans is not None else None
        self.on_update()
