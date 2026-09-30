"""Merged index: the machine index (+ recipes) > help. `load` reads it; the pipeline's tournament queries it."""
from __future__ import annotations

import shutil
from pathlib import Path

from jiffy.config import CORPUS_DIR
from jiffy.corpus import helpscan, machine, recipes
from jiffy.types import Doc

DEFAULT_SOURCES = ("machine", "recipes", "help")


def load(sources: tuple[str, ...] | list[str] = DEFAULT_SOURCES, corpus_dir: Path = CORPUS_DIR) -> dict[str, Doc]:
    """Merge by precedence machine > help. Nothing is rebuilt here: `jiffy index` does that.
    A recipe joins only a command the machine index has."""
    docs: dict[str, Doc] = {}
    if "machine" in sources:
        docs.update(machine.load(corpus_dir))
    if "recipes" in sources:
        docs = recipes.merge(docs, {n: r for n, r in recipes.load().items() if n in docs})
    if "help" in sources:
        for n, d in helpscan.load(corpus_dir).items():
            docs.setdefault(n, d)
    return docs


_installed: dict[str, bool] = {}


def installed(name: str) -> bool:
    """Is the command (first word of `name`) on PATH? Memoised per process."""
    head = name.split()[0]
    if head not in _installed:
        _installed[head] = shutil.which(head) is not None
    return _installed[head]
