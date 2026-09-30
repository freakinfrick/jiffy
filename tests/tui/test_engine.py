"""The ENTER-triggered run, and cancellation: the prompt never waits on the graph."""
from __future__ import annotations

import threading
import time

from jiffy.tui.engine import Engine, options_for


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_typing_costs_nothing_and_run_runs_once(answer_fn):
    seen: list[str] = []

    def counted(q, **kw):
        seen.append(q)
        return answer_fn(q)

    eng = Engine(answer_fn=counted).start()
    try:
        for text in ("f", "fi", "fin", "find big files"):
            eng.request(text)
            time.sleep(0.01)
        assert seen == []                    # typing never searches
        assert eng.turn is None
        eng.run("find big files")
        assert _wait(lambda: eng.turn is not None)
        assert seen == ["find big files"]    # ENTER runs exactly once
    finally:
        eng.stop()


def test_a_superseded_run_is_discarded(answer_fn):
    started = threading.Event()
    release = threading.Event()

    def slow(q, **kw):
        if q == "slow query":
            started.set()
            release.wait(5)
        return answer_fn(q)

    eng = Engine(answer_fn=slow).start()
    try:
        eng.run("slow query")
        assert started.wait(5)
        eng.run("find big files")       # a second ENTER supersedes the first run
        release.set()
        assert _wait(lambda: eng.turn is not None and eng.turn.query == "find big files")
        assert eng.turn.query == "find big files"
    finally:
        eng.stop()


def test_answered_is_false_until_the_turn_lands(answer_fn):
    eng = Engine(answer_fn=answer_fn).start()
    try:
        assert eng.answered("find big files") is False   # nothing searched yet
        assert eng.pending is False
        eng.run("find big files")
        assert _wait(lambda: eng.answered("find big files"))
        assert eng.turn is not None
        assert eng.pending is False
    finally:
        eng.stop()


def test_run_is_idempotent_for_the_same_text(answer_fn):
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(q, **kw):
        calls.append(q)
        started.set()
        release.wait(2.0)
        return answer_fn(q)

    eng = Engine(answer_fn=slow).start()
    try:
        eng.run("find big files")
        assert started.wait(2.0)
        eng.run("find big files")           # already in flight for this exact text
        assert len(calls) == 1              # not restarted
        release.set()
        assert _wait(lambda: eng.turn is not None)
        assert len(calls) == 1
    finally:
        release.set()
        eng.stop()


def test_a_judge_explosion_is_not_a_ui_failure():
    def boom(q, **kw):
        raise RuntimeError("judge on fire")

    eng = Engine(answer_fn=boom).start()
    try:
        eng.run("anything")
        assert _wait(lambda: bool(eng.error))
        assert eng.turn is None
        assert "judge on fire" in eng.error
    finally:
        eng.stop()


def test_options_expose_the_alternates_in_cycle_order(answer_fn):
    ans = answer_fn("find files bigger than a size")
    opts = options_for(ans, "find files bigger than a size")
    assert opts[0].assembled is ans.assembled          # the winner, as the graph built it
    assert all(o.verdict is not None for o in opts)
    assert len({o.assembled.line for o in opts}) == len(opts)


def test_running_is_true_only_while_a_run_is_in_flight(answer_fn):
    started = threading.Event()
    release = threading.Event()

    def slow(q, **kw):
        started.set()
        release.wait(2.0)
        return answer_fn(q)

    eng = Engine(answer_fn=slow).start()
    try:
        assert eng.running is False and eng.elapsed == 0.0
        eng.run("find big files")
        assert started.wait(2.0)
        assert eng.running is True
        release.set()
        assert _wait(lambda: not eng.running)
        assert eng.turn is not None            # and it is False again afterwards
    finally:
        release.set()
        eng.stop()


def test_elapsed_is_zero_when_idle_and_positive_in_flight(answer_fn):
    """The horizon counts the run only, and never a pre-run wait."""
    started = threading.Event()
    release = threading.Event()

    def slow(q, **kw):
        started.set()
        release.wait(2.0)
        return answer_fn(q)

    eng = Engine(answer_fn=slow).start()
    try:
        eng.run("find big files")
        assert started.wait(2.0)
        time.sleep(0.05)
        assert eng.running is True
        assert eng.elapsed > 0.0
    finally:
        release.set()
        eng.stop()


def test_an_update_fires_when_the_run_starts(answer_fn):
    """The UI has to repaint to show the horizon."""
    release = threading.Event()
    ticks: list[bool] = []

    def slow(q, **kw):
        release.wait(2.0)
        return answer_fn(q)

    eng = Engine(answer_fn=slow,
                 on_update=lambda: ticks.append(True)).start()
    try:
        eng.run("find big files")
        assert _wait(lambda: ticks and eng.running)
    finally:
        release.set()
        eng.stop()


def test_editing_cancels_the_run_in_flight(answer_fn):
    """A superseded query stops paying: the pipeline sees the token turn True."""
    seen: list[bool] = []
    started = threading.Event()
    release = threading.Event()

    def slow(q, cancelled=None):
        started.set()
        release.wait(2.0)
        seen.append(bool(cancelled and cancelled()))
        return answer_fn(q)

    eng = Engine(answer_fn=slow).start()
    try:
        eng.run("find big")
        assert started.wait(2.0)
        eng.request("find big log files")       # the user kept typing
        release.set()
        assert _wait(lambda: seen)
        assert seen[0] is True                  # the run knew it was unwanted
    finally:
        release.set()
        eng.stop()


def test_editing_clears_the_answer_on_screen(answer_fn):
    """An answer to older text is an answer to a question nobody asked."""
    eng = Engine(answer_fn=answer_fn).start()
    try:
        eng.run("find big files")
        assert _wait(lambda: eng.turn is not None)
        eng.request("find big files and")
        assert eng.turn is None                 # immediately, not when the next run lands
        assert eng.current() is None
    finally:
        eng.stop()


def test_a_cancelled_run_is_not_an_error(answer_fn):
    """`Cancelled` is control flow. It must never reach the footer as red text."""
    from jiffy.graph.cancel import Cancelled

    def boom(q, cancelled=None):
        raise Cancelled()

    eng = Engine(answer_fn=boom).start()
    try:
        eng.run("find big files")
        assert _wait(lambda: not eng.running and eng._running_gen == eng._gen)
        assert eng.error == ""
        assert eng.turn is None
    finally:
        eng.stop()


def test_a_stub_without_the_cancelled_parameter_still_works(answer_fn):
    """A caller that takes only the query still works; it just cannot be cancelled."""
    eng = Engine(answer_fn=lambda q: answer_fn(q)).start()
    try:
        assert eng._takes_cancel is False
        eng.run("find big files")
        assert _wait(lambda: eng.turn is not None)
    finally:
        eng.stop()
