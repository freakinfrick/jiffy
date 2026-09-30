"""The held-out cases stay held out.

`fresh_cases.json` (2026-09-23) was written without looking at the corpus, after the
p12 set had been tuned on long enough to act as training data. Two rules keep it
honest: it shares no question with the older sets, and no shipped recipe is worded
like one of its questions. A recipe that fails here was written from the bench.
"""
from __future__ import annotations

import re

from jiffy.bench.cases import ROOT, gold, load_cases
from jiffy.corpus import recipes

CASES = ROOT / "bench" / "cases"
FRESH = CASES / "fresh_cases.json"
LIMIT = 0.5          # word-set Jaccard; a copied question sits at 1.0, a paraphrase near 0.6

_STOP = frozenset("a an the to in of i me my is and or it this that for on with".split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - _STOP


def _overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa | wb else 0.0


def _closest(text: str, questions: list[str]) -> tuple[float, str]:
    return max((_overlap(text, q), q) for q in questions)


def test_fresh_cases_are_well_formed():
    cases = load_cases(FRESH)
    assert len(cases) >= 100
    assert len({c["q"] for c in cases}) == len(cases)
    assert all(c["split"] == "heldout" and c["area"] and gold(c) for c in cases)


def test_fresh_cases_share_no_question_with_the_older_sets():
    older = [c["q"] for f in ("p12_cases.json", "line_cases.json") for c in load_cases(CASES / f)]
    near = [(q, *_closest(q, older)) for q in (c["q"] for c in load_cases(FRESH))]
    assert [n for n in near if n[1] > LIMIT] == []


def test_no_recipe_is_worded_like_a_held_out_question():
    fresh = [c["q"] for c in load_cases(FRESH)]
    near = [(name, e.does, *_closest(e.does, fresh))
            for name, doc in recipes.load().items() for e in doc.examples]
    assert [n for n in near if n[2] > LIMIT] == []

