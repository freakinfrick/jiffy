"""The gate's tier table, driven row by row. Data lives in `tier_table.py`."""
from __future__ import annotations

import pytest

from jiffy.gate import banner, classify, evaluate, may_run
from jiffy.types import Tier
from tier_table import REQUIRED, TABLE


def test_table_is_big_enough_to_mean_something():
    assert len(TABLE) >= 30
    assert len({line for line, _ in TABLE}) == len(TABLE)      # no duplicate rows
    assert {tier for _, tier in TABLE} == set(Tier)            # every tier exercised


@pytest.mark.parametrize("line,expected", TABLE, ids=[ln for ln, _ in TABLE])
def test_tier(line: str, expected: Tier):
    verdict = classify(line)
    assert verdict.tier is expected, f"{line!r} -> {verdict}"


@pytest.mark.parametrize("line,expected", REQUIRED, ids=[ln for ln, _ in REQUIRED])
def test_required_rows_from_the_brief(line: str, expected: Tier):
    """The eight lines the brief names, asserted outside the big table too."""
    assert classify(line).tier is expected


@pytest.mark.parametrize("line,expected", TABLE, ids=[ln for ln, _ in TABLE])
def test_non_clean_verdicts_name_a_reason_and_a_rule(line: str, expected: Tier):
    verdict = classify(line)
    if verdict.tier is Tier.CLEAN:
        assert verdict.reason == "" and verdict.rule_id == ""
    else:
        assert verdict.reason, f"{line!r} blocked with no reason to show"
        assert verdict.rule_id, f"{line!r} blocked with no rule id"
        assert verdict.reason in banner(verdict)


@pytest.mark.parametrize("line,expected", TABLE, ids=[ln for ln, _ in TABLE])
def test_classification_is_deterministic(line: str, expected: Tier):
    first = classify(line)
    assert all(classify(line) == first for _ in range(5))


def test_jiffy_trailing_comment_does_not_change_the_tier():
    """jiffy appends `  # request`; the gate must see through its own suffix."""
    for line, expected in TABLE:
        assert classify(f"{line}  # what the user typed").tier is expected


def test_deny_lines_may_never_run():
    for line, expected in TABLE:
        if expected is Tier.DENY:
            verdict = classify(line)
            assert not may_run(verdict)
            assert not may_run(verdict, confirmed=True)


def test_confirm_needs_the_second_enter():
    verdict = classify("rm -rf ./build")
    assert verdict.tier is Tier.CONFIRM
    assert not may_run(verdict)
    assert may_run(verdict, confirmed=True)


def test_note_and_clean_run_without_ceremony():
    assert may_run(classify("rm notes.txt"))
    assert may_run(classify("ls -la"))


def test_evaluate_without_a_judge_is_exactly_classify():
    for line, _ in TABLE:
        assert evaluate(line) == classify(line)
        assert evaluate(line, judge_p=None) == classify(line)
