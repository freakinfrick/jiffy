"""The judge interface. A better judge drops in here and touches nothing else.

Three primitives, because that is what every shipped node needs and nothing more:
`choice` (listwise: pick one of N), `noul` (pointwise: probability of yes), and
`batch` (run many jobs concurrently, order preserved). The pipeline and the gate
never import a concrete adapter — they ask `get(name)`.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, Sequence

__all__ = ["JudgeAdapter", "register", "get", "available", "REGISTRY"]


class JudgeAdapter(ABC):
    """One backend: jev (paid API) today; a local judge drops in here later.

    Implementations accumulate spend into `cost_usd` (0.0 for a local backend) so
    the pipeline can meter a run against jiffy's own cap without reaching into a
    backend's ledger.
    """

    #: registry key; set by @register, overridable by a subclass
    _name: str = ""

    def __init__(self) -> None:
        self.cost_usd: float = 0.0

    @property
    def name(self) -> str:
        """Registry key this adapter answers to."""
        return self._name or type(self).__name__.lower()

    @abstractmethod
    def choice(
        self,
        state: dict[str, Any],
        instructions: str,
        options: Sequence[str],
    ) -> dict[str, float]:
        """Listwise: one probability per option key. Keys are `options`, sum ~1."""

    @abstractmethod
    def noul(
        self,
        state: dict[str, Any],
        instructions: str,
        yes: str | None = None,
        no: str | None = None,
    ) -> float:
        """Pointwise: probability that `instructions` holds for `state`, in [0, 1]."""

    @abstractmethod
    def batch(self, jobs: Iterable[Callable[[], Any]]) -> list:
        """Run `jobs` concurrently; return results in the order given.

        A job is a zero-arg callable wrapping a `choice` or `noul` call. Failures
        surface as the exception object in that slot — the pipeline degrades, it
        does not crash.
        """


_REGISTRY: dict[str, type[JudgeAdapter]] = {}
REGISTRY = _REGISTRY   # public alias of the same dict (name -> adapter class)


def register(name: str) -> Callable[[type[JudgeAdapter]], type[JudgeAdapter]]:
    """Class decorator: `@register("jev")`."""

    def deco(cls: type[JudgeAdapter]) -> type[JudgeAdapter]:
        cls._name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get(name: str, config: Any = None) -> JudgeAdapter:
    """Instantiate the adapter registered under `name`.

    `config` reaches adapters that take one. Without it they reload config from
    disk, and a caller's in-memory override (`--usd-cap`) silently never applies.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown judge {name!r}; have: {', '.join(sorted(_REGISTRY)) or '(none)'}") from None
    if config is not None and "config" in inspect.signature(cls).parameters:
        return cls(config=config)
    return cls()


def available() -> list[str]:
    return sorted(_REGISTRY)
