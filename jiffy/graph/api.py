"""`answer(query) -> Answer`. The TUI and the bench call this.

Everything else — index, config, judge — is resolved here once and cached, so a
frontend never assembles a pipeline and the two callers cannot drift apart.
Keyword-only escapes (`docs`, `cfg`, `judge_factory`) exist for tests and the bench.
"""
from __future__ import annotations

from typing import Callable

from jiffy.config import Config, load as load_config
from jiffy.corpus import index as corpus_index
from jiffy.judge.base import JudgeAdapter
from jiffy.types import Answer, Doc

from .pipeline import Pipeline

__all__ = ["answer", "build", "corpus", "reset_cache"]

_docs: dict[str, Doc] | None = None
_pipelines: dict[tuple, Pipeline] = {}


def corpus(cfg: Config | None = None) -> dict[str, Doc]:
    """The merged index, loaded once per process."""
    global _docs
    if _docs is None:
        cfg = cfg or load_config()
        _docs = corpus_index.load(corpus_dir=cfg.corpus_dir)
    return _docs


def reset_cache() -> None:
    """Drop the cached index and pipelines (tests, and `jiffy index`)."""
    global _docs
    _docs = None
    _pipelines.clear()


def build(*, cfg: Config | None = None, docs: dict[str, Doc] | None = None,
             judge_factory: Callable[[str], JudgeAdapter] | None = None, cache: bool = True) -> Pipeline:
    """Build (or reuse) the pipeline for this index, config and judge."""
    cfg = cfg or load_config()
    if docs is None:
        docs = corpus(cfg)
    key = (id(docs), cfg.judge, cfg.node_timeout_s, cfg.usd_cap, judge_factory)
    if cache and key in _pipelines:
        return _pipelines[key]
    p = Pipeline(docs, cfg=cfg, judge_factory=judge_factory)
    if cache:
        _pipelines[key] = p
    return p


def answer(query: str, cancelled: Callable[[], bool] | None = None, **kw) -> Answer:
    """One turn: tournament, pick, invoke, critic, compose, fill, gate. Always returns an Answer.

    Never raises for a judge failure — a stage that errors degrades and the trace
    says so. The one exception is `cancelled`: when that predicate turns True the
    run raises `graph.cancel.Cancelled`, because a superseded query has no answer
    worth building and no budget worth spending.
    """
    return build(**kw).run(query, cancelled=cancelled)
