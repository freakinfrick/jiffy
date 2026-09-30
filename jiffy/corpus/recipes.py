"""Recipes: lines jiffy ships because no tldr page has them.

Public IP (`curl -s https://ifconfig.me`) or "biggest folders here"
(`du -sh */ | sort -rh | head`) are pipelines and idioms. Compose builds one command
from its flags, so it can never write them, and tldr does not document them.
Recipes are hand-written pages in tldr's own markdown (`recipes/*.md`), shipped
inside the package so every install has them, and in a format tldr could take
upstream unchanged.

A recipe joins its command's existing page *ahead* of the tldr examples: pick
shows the judge only a page's first few uses, so a recipe at the back would
never be seen. A recipe for a command this machine does not have is dropped.
Recipes exist for idioms tldr lacks, never to reword a command until a bench
question matches it (tests/bench/test_fresh_cases.py enforces the held-out side).
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from jiffy.corpus import tldr
from jiffy.types import Doc

__all__ = ["DIR", "parse", "load", "merge"]

DIR = Path(__file__).with_name("recipes")

def parse(text: str) -> Doc | None:
    """One tldr-format page -> Doc(source="recipe"). None without a title or an example."""
    return tldr.parse(text, "common", "recipe")


def load(directory: Path = DIR) -> dict[str, Doc]:
    docs: dict[str, Doc] = {}
    for p in sorted(directory.glob("*.md")):
        d = parse(p.read_text())
        if d is not None:
            docs[d.name] = d
    return docs


def merge(docs: dict[str, Doc], recipes: dict[str, Doc]) -> dict[str, Doc]:
    """Recipes go first on their command's page; a page's own examples follow."""
    out = dict(docs)
    for name, r in recipes.items():
        page = out.get(name)
        if page is None:
            out[name] = r
        else:
            known = {e.line for e in page.examples}
            fresh = tuple(e for e in r.examples if e.line not in known)
            out[name] = replace(page, examples=fresh + tuple(page.examples))
    return out
