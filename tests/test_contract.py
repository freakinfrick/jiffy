"""The contract itself: if these fail, downstream units are building on sand.

Field names are checked literally. Renaming a field here is a contract change,
not a refactor — every unit importing jiffy.types is affected.
"""
import dataclasses
import inspect

import pytest

from jiffy import types as T
from jiffy.judge.base import JudgeAdapter, get, register

EXPECTED_FIELDS = {
    "Example": ["does", "line"],
    "Doc": ["name", "plat", "desc", "examples", "source"],
    "Candidate": ["name", "doc", "scores"],
    "Assembled": ["line", "request", "comment_line", "placeholders", "fills"],
    "GateVerdict": ["tier", "reason", "rule_id"],
    "NodeTrace": ["node_id", "kind", "judge", "in_n", "out_n", "ms", "usd", "degraded", "detail"],
    "Trace": ["query", "nodes", "total_ms", "total_usd"],
    "Answer": ["assembled", "candidate", "alternates", "verdict", "trace"],
}


@pytest.mark.parametrize("name,fields", EXPECTED_FIELDS.items())
def test_dataclass_fields(name, fields):
    cls = getattr(T, name)
    assert dataclasses.is_dataclass(cls), f"{name} is not a dataclass"
    assert [f.name for f in dataclasses.fields(cls)] == fields


@pytest.mark.parametrize("name", EXPECTED_FIELDS)
def test_dataclasses_are_frozen(name):
    assert getattr(T, name).__dataclass_params__.frozen, f"{name} must be frozen"


def test_tier_values_and_order():
    assert (T.Tier.CLEAN, T.Tier.NOTE, T.Tier.CONFIRM, T.Tier.DENY) == (0, 1, 2, 3)
    assert T.Tier.DENY > T.Tier.CONFIRM > T.Tier.NOTE > T.Tier.CLEAN


def test_trace_renders():
    tr = T.Trace(query="list big files", nodes=[
        T.NodeTrace("retrieve", "retrieve", "jev", 4735, 96, 812.0, 0.005, False, "chunks=32"),
    ], total_ms=812.0, total_usd=0.005)
    out = tr.render()
    assert "retrieve" in out and "4735->96" in out and "list big files" in out and "$0.0050" in out


def test_judge_adapter_has_exactly_three_abstract_methods():
    assert set(JudgeAdapter.__abstractmethods__) == {"choice", "noul", "batch"}


def test_judge_adapter_signatures():
    def sig(m):
        return list(inspect.signature(getattr(JudgeAdapter, m)).parameters)

    assert sig("choice") == ["self", "state", "instructions", "options"]
    assert sig("noul") == ["self", "state", "instructions", "yes", "no"]
    assert sig("batch") == ["self", "jobs"]
    noul = inspect.signature(JudgeAdapter.noul).parameters
    assert noul["yes"].default is None and noul["no"].default is None


def test_name_and_cost_are_not_abstract():
    assert "name" not in JudgeAdapter.__abstractmethods__
    assert isinstance(JudgeAdapter.name, property)


def test_registry_roundtrip():
    @register("_contract_fake")
    class Fake(JudgeAdapter):
        def choice(self, state, instructions, options):
            return {o: 1.0 / len(options) for o in options}

        def noul(self, state, instructions, yes=None, no=None):
            return 0.5

        def batch(self, jobs):
            return [j() for j in jobs]

    j = get("_contract_fake")
    assert j.name == "_contract_fake"
    assert j.cost_usd == 0.0
    assert j.choice({}, "", ["a", "b"]) == {"a": 0.5, "b": 0.5}
    with pytest.raises(KeyError):
        get("_no_such_judge")


def test_incomplete_adapter_cannot_instantiate():
    class Half(JudgeAdapter):
        def choice(self, state, instructions, options):
            return {}

    with pytest.raises(TypeError):
        Half()
