"""Cooperative cancellation for a run nobody wants any more.

A pipeline stage runs on a thread that cannot be killed, so "cancel" can only mean
"stop before starting the next thing". That is enough to matter: the stages are
the unit of cost, and a superseded query that stops at the next boundary does
not pay for the judge calls the rest of the pipeline would have made.

The pipeline checks between stages and before each judged stage's thread starts.
`Cancelled` is NOT a degradation: it is a BaseException, so the pipeline's
degrade-and-continue path never catches it and turns an abandoned run into a
half-answer.
"""
from __future__ import annotations

from typing import Callable

__all__ = ["Cancelled", "CancelToken", "NEVER"]


class Cancelled(BaseException):
    """The run was superseded. Raised out of `Pipeline.run`; never traced, never shown."""


class CancelToken:
    """One run's liveness. `cancel()` is one-way and safe from any thread."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def __call__(self) -> bool:
        return self._cancelled

    @property
    def cancelled(self) -> bool:
        return self._cancelled


def NEVER() -> bool:            # noqa: N802 - a sentinel callable, not a class
    """The default predicate: this run is always wanted."""
    return False


def check(cancelled: Callable[[], bool] | None) -> None:
    """Raise `Cancelled` if the caller has lost interest."""
    if cancelled is not None and cancelled():
        raise Cancelled()
