"""Pure scoring maths. No I/O, no judge, no network."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from .cases import gold


def hit(case: dict, chosen: str) -> bool:
    """Top-1 is correct if the chosen command name matches ANY gold entry."""
    return chosen in gold(case)


def contains(case: dict, names: Iterable[str]) -> bool:
    """Did any gold command survive into this candidate set?"""
    g = set(gold(case))
    return any(n in g for n in names)


def rate(flags: Sequence[bool]) -> float:
    return sum(1 for f in flags if f) / len(flags) if flags else 0.0


def percentile(xs: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]."""
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def monotonic(recalls: Sequence[float], eps: float = 1e-12) -> bool:
    """Recall down the stages may only fall: a dropped candidate cannot come back."""
    return all(b <= a + eps for a, b in zip(recalls, recalls[1:]))
