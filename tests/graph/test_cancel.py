"""A superseded run stops spending, and never returns a half-answer."""
from __future__ import annotations

import pytest

from jiffy.graph import Pipeline
from jiffy.graph import pipeline as P
from jiffy.graph.cancel import Cancelled, CancelToken

from conftest import Scripted

QUERY = "find big log files"


def _pipe(docs, cfg, judge):
    return Pipeline(docs, cfg=cfg, judge_factory=lambda name: judge)


def test_a_token_starts_live_and_cancels_once():
    token = CancelToken()
    assert token() is False and token.cancelled is False
    token.cancel()
    assert token() is True and token.cancelled is True


def test_cancelled_is_not_a_judge_failure_the_pipeline_can_swallow():
    """It must escape the degrade path, or an abandoned run becomes a half-answer."""
    assert not issubclass(Cancelled, Exception)
    assert issubclass(Cancelled, BaseException)


def test_an_already_cancelled_run_does_nothing_at_all(docs, cfg):
    j = Scripted(usd_per_call=0.01)
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        _pipe(docs, cfg, j).run(QUERY, cancelled=token)
    assert j.calls == 0


def test_cancelling_mid_run_stops_before_the_next_stage(docs, cfg, monkeypatch):
    """The stage in flight finishes; the remaining stages never run."""
    token = CancelToken()
    calls: list[str] = []

    def wrap(name, fn):
        def run(r, j):
            calls.append(name)
            token.cancel()               # the user typed another character
            return fn(r, j)
        return run
    monkeypatch.setattr(P, "STAGES", tuple((n, wrap(n, fn), judged) for n, fn, judged in P.STAGES))

    with pytest.raises(Cancelled):
        _pipe(docs, cfg, Scripted()).run(QUERY, cancelled=token)
    assert calls == ["retrieve"]         # exactly one stage ran, then the run stopped


def test_an_uncancelled_run_is_unaffected(docs, cfg):
    ans = _pipe(docs, cfg, Scripted()).run(QUERY, cancelled=CancelToken())
    assert ans.assembled.line
    assert ans.verdict is not None


def test_cancelled_defaults_to_never(docs, cfg):
    """No token is the bench's and the tests' path: nothing polls, nothing raises."""
    assert _pipe(docs, cfg, Scripted()).run(QUERY).assembled.line
