"""The raise-only rule: a judge may promote a tier and can do nothing else."""
from __future__ import annotations

import math
import random

import pytest

from jiffy.gate import classify, evaluate, raise_tier
from jiffy.gate.classify import JUDGE_RULE_ID, RAISE_CONFIRM_P, RAISE_NOTE_P
from jiffy.types import GateVerdict, Tier
from tier_table import TABLE

ALL_VERDICTS = [
    GateVerdict(tier=t, reason=f"reason {t.name}", rule_id=f"builtin:{t.name.lower()}")
    for t in Tier
]


@pytest.mark.parametrize("verdict", ALL_VERDICTS, ids=[v.tier.name for v in ALL_VERDICTS])
def test_never_lowers_for_any_probability(verdict: GateVerdict):
    """Property: for every tier and every p, out.tier >= in.tier."""
    rng = random.Random(0)
    probabilities = [
        0.0, 1.0, 0.5, RAISE_NOTE_P, RAISE_CONFIRM_P,
        -1.0, -1e9, 1e9, 2.0, 0.999999, 1e-12,
        float("nan"), float("inf"), float("-inf"),
    ] + [rng.uniform(-5, 5) for _ in range(2000)]
    for p in probabilities:
        out = raise_tier(verdict, p)
        assert out.tier >= verdict.tier, f"p={p!r} lowered {verdict.tier} to {out.tier}"


@pytest.mark.parametrize("verdict", ALL_VERDICTS, ids=[v.tier.name for v in ALL_VERDICTS])
def test_garbage_from_the_judge_is_not_a_shell_failure(verdict: GateVerdict):
    """A broken model returns junk. Junk must leave the pattern verdict standing."""
    for junk in (None, "", "yes", object(), [], {}, float("nan")):
        out = raise_tier(verdict, junk)      # type: ignore[arg-type]
        assert out.tier >= verdict.tier
        assert out.tier <= max(verdict.tier, Tier.CONFIRM)


@pytest.mark.parametrize("p", [0.0, 0.5, RAISE_NOTE_P, 0.9, 1.0, 5.0, math.inf])
def test_judge_can_never_reach_deny(p: float):
    """No probability promotes a non-DENY line to DENY. DENY is pattern-only."""
    for verdict in ALL_VERDICTS:
        out = raise_tier(verdict, p)
        if verdict.tier is not Tier.DENY:
            assert out.tier is not Tier.DENY
        else:
            assert out is verdict          # a DENY verdict passes through untouched


def test_judge_cannot_soften_a_deny_line():
    for line, expected in TABLE:
        if expected is not Tier.DENY:
            continue
        for p in (0.0, 0.1, 0.49, 1.0):
            assert evaluate(line, judge_p=p).tier is Tier.DENY


def test_raise_thresholds_promote_as_documented():
    clean = GateVerdict(tier=Tier.CLEAN, reason="", rule_id="")
    assert raise_tier(clean, 0.1).tier is Tier.CLEAN
    assert raise_tier(clean, RAISE_NOTE_P).tier is Tier.NOTE
    assert raise_tier(clean, RAISE_CONFIRM_P).tier is Tier.CONFIRM
    assert raise_tier(clean, 0.99).rule_id == JUDGE_RULE_ID


def test_an_unchanged_tier_keeps_the_original_reason():
    verdict = classify("rm -rf ./build")
    assert raise_tier(verdict, 0.99) is verdict       # already CONFIRM: untouched


def test_raise_tier_does_not_mutate_its_input():
    for verdict in ALL_VERDICTS:
        before = (verdict.tier, verdict.reason, verdict.rule_id)
        raise_tier(verdict, 0.99)
        assert (verdict.tier, verdict.reason, verdict.rule_id) == before


def test_raising_is_monotone_in_p():
    """More judge confidence never produces a lower tier than less confidence."""
    clean = GateVerdict(tier=Tier.CLEAN, reason="", rule_id="")
    tiers = [raise_tier(clean, p / 100).tier for p in range(0, 101)]
    assert tiers == sorted(tiers)
