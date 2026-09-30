import pytest

from jiffy.bench.cases import gold, load_cases
from jiffy.bench.scoring import contains, hit, monotonic, percentile, rate


def test_gold_normalises_str_and_list():
    assert gold({"cmd": "ls"}) == ["ls"]
    assert gold({"cmd": ["grep", "rg"]}) == ["grep", "rg"]


def test_hit_scores_list_gold_and_str_gold():
    """The 0.233 bug: comparing against the raw field scores every list-gold case as a miss."""
    assert hit({"cmd": "ls"}, "ls")
    assert not hit({"cmd": "ls"}, "du")
    assert hit({"cmd": ["grep", "rg", "ag"]}, "rg")
    assert hit({"cmd": ["grep", "rg", "ag"]}, "grep")
    assert not hit({"cmd": ["grep", "rg", "ag"]}, "ls")
    assert not hit({"cmd": ["grep", "rg"]}, "")        # sentinel 'nothing matched' never hits


def test_shipped_cases_shape():
    cases = load_cases()
    assert len(cases) == 60
    assert sum(isinstance(c["cmd"], str) for c in cases) == 15
    assert sum(isinstance(c["cmd"], list) for c in cases) == 45


def test_contains_any_gold():
    assert contains({"cmd": ["a", "b"]}, ["x", "b"])
    assert not contains({"cmd": "a"}, ["x", "y"])
    assert not contains({"cmd": "a"}, [])


def test_rate_and_percentile():
    assert rate([True, False, True, True]) == 0.75
    assert rate([]) == 0.0
    assert percentile([10, 20, 30, 40, 50], 50) == 30
    assert percentile([0, 100], 95) == pytest.approx(95)
    assert percentile([7], 95) == 7
    assert percentile([], 50) == 0.0


def test_monotonic():
    assert monotonic([0.9, 0.9, 0.7, 0.3])
    assert not monotonic([0.9, 0.7, 0.8])
