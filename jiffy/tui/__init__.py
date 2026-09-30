"""The product: a one-line prompt. Everything behind it is plumbing.

`jiffy.cli` calls `run()`. The pieces are separable so they can be tested
headless: `engine` owns the cancellable, ENTER-triggered graph run; `keys` owns the
binding registry; `shell` is the single chokepoint that can execute anything.
"""
from .app import Tui, run

__all__ = ["Tui", "run"]
