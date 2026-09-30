"""The pipeline: stage maths against scripted judges, and the runner's three promises —
degrade never hard-fail, determinism, the gate is terminal. No network, no money."""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import replace

import pytest

from jiffy.fill import Sources, resolve as fill_resolve
from jiffy.graph import Pipeline, Run, STAGES, answer, assemble, placeholders
from jiffy.graph import pipeline as P
from jiffy.types import Candidate, Tier

from conftest import Scripted

QUERY = "find big log files"


def run_for(docs, cfg, query=QUERY, cands=None):
    return Run(query=query, docs=docs, cfg=cfg, cands=cands if cands is not None else [])


def cand(docs, name):
    return Candidate(name, docs[name])


def ask(docs, cfg, judge, query=QUERY):
    return Pipeline(docs, cfg=cfg, judge_factory=lambda name: judge).run(query)


def strip_timings(trace_text: str) -> str:
    return re.sub(r"\s*\d+ms", " MS", trace_text)


# ---------------------------------------------------------------- retrieve: the tournament


def test_the_tournament_sends_the_top_of_every_chunk_to_the_final(docs, cfg, monkeypatch):
    monkeypatch.setattr(P, "CHUNK", 2)
    monkeypatch.setattr(P, "PER_CHUNK", 1)
    j = Scripted(choices={"du": 5.0, "find": 1.0, "grep": 1.0, "rm": 3.0})
    r = run_for(docs, cfg)
    out = P.retrieve(r, j)
    assert r.detail["chunks"] == 2 and j.calls == 2          # du,find | grep,rm
    assert [c.name for c in out] == ["du", "rm"]             # best of each, best first
    assert out[0].scores["retrieve"] > out[1].scores["retrieve"]


def test_the_tournament_shows_the_judge_every_indexed_command(docs, cfg):
    seen = []

    class Spy(Scripted):
        def choice(self, state, instructions, options):
            seen.extend(state["candidates"].values())
            return super().choice(state, instructions, options)

    P.retrieve(run_for(docs, cfg), Spy())
    assert sorted(s.split(":")[0] for s in seen) == sorted(docs)
    assert "find: Find files and directories under a path" in seen


def test_a_failed_chunk_loses_only_its_own_finalists(docs, cfg, monkeypatch):
    monkeypatch.setattr(P, "CHUNK", 2)

    class Half(Scripted):
        def choice(self, state, instructions, options):
            if "du: Estimate file space usage" in state["candidates"].values():
                raise RuntimeError("chunk down")
            return super().choice(state, instructions, options)

    r = run_for(docs, cfg)
    out = P.retrieve(r, Half())
    assert r.detail["failed"] == 1 and {c.name for c in out} == {"grep", "rm"}


def test_an_empty_index_retrieves_nothing_and_asks_nothing(cfg):
    j = Scripted()
    assert P.retrieve(run_for({}, cfg), j) == [] and j.calls == 0


# ---------------------------------------------------------------- pick / invoke / critic


def test_pick_ranks_the_finalists_by_probability(docs, cfg):
    r = run_for(docs, cfg, cands=[cand(docs, n) for n in ("grep", "find", "du")])
    out = P.pick(r, Scripted(choices={"find": 6.0, "grep": 1.0, "du": 3.0}))
    assert [c.name for c in out] == ["find", "du", "grep"]
    assert out[0].scores["pick"] == pytest.approx(0.6)


def test_invoke_picks_the_documented_line_the_judge_favours(docs, cfg):
    j = Scripted(choices={"find . -size +100M": 9.0, "find . -name '{{pattern}}'": 1.0})
    r = run_for(docs, cfg, cands=[cand(docs, "find")])
    P.invoke(r, j)
    assert r.line == "find . -size +100M" and r.signals["invocation_p"] == pytest.approx(0.9)


def test_critic_falls_back_to_the_next_candidate_when_the_line_fails(docs, cfg):
    j = Scripted(nouls={"du -sh *": 0.1, "find . -name '{{pattern}}'": 0.95})
    r = run_for(docs, cfg, cands=[cand(docs, "du"), cand(docs, "find")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    out = P.critic(r, j)
    assert r.detail["fell_back"] is True and r.detail["shipped"] == "find"
    assert out[0].name == "find" and r.line.startswith("find")


def test_critic_keeps_a_passing_line_and_never_walks(docs, cfg):
    r = run_for(docs, cfg, cands=[cand(docs, "du"), cand(docs, "find")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    out = P.critic(r, Scripted(nouls={"du -sh *": 0.9}))
    assert r.detail["fell_back"] is False and out[0].name == "du"


def test_critic_fallback_depth_is_capped(docs, cfg):
    r = run_for(docs, cfg, cands=[cand(docs, n) for n in ("du", "find", "grep", "rm")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    out = P.critic(r, Scripted(noul=0.01))                # nothing clears the bar
    assert len(r.detail["graded"]) == 1 + P.CRITIC_DEPTH
    assert r.detail["below_threshold"] is True
    assert out[0].name == "du" and r.line == "du -sh *"   # incumbent stands, flagged


def test_critic_keeps_the_incumbent_when_none_clears_the_bar(docs, cfg):
    """Even when a fallback grades higher: promoting it cost 0.07 top-1 (niche tools won)."""
    r = run_for(docs, cfg, cands=[cand(docs, "du"), cand(docs, "find")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    out = P.critic(r, Scripted(nouls={"du -sh *": 0.13, "find . -name '{{pattern}}'": 0.35}))
    assert r.detail["below_threshold"] is True and r.detail["fell_back"] is False
    assert out[0].name == "du" and r.line == "du -sh *"


def test_critic_ships_an_installed_line_over_a_missing_one_when_both_clear(docs, cfg, monkeypatch):
    """`ipinfo myip` (0.53, not installed) lost to nothing; now the runnable line wins."""
    monkeypatch.setattr(P, "installed", lambda name: name != "du")
    j = Scripted(nouls={"du -sh *": 0.9, "find . -name '{{pattern}}'": 0.6})
    r = run_for(docs, cfg, cands=[cand(docs, "du"), cand(docs, "find")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    assert P.critic(r, j)[0].name == "find" and r.detail["installed"] is True
    monkeypatch.setattr(P, "installed", lambda name: False)   # nothing runs here: first clearing stands
    r = run_for(docs, cfg, cands=[cand(docs, "du"), cand(docs, "find")])
    r.line, r.line_does = "du -sh *", "Show sizes"
    assert P.critic(r, j)[0].name == "du" and r.detail["installed"] is False


def test_a_winner_with_no_examples_goes_straight_to_compose(docs, cfg):
    from jiffy.types import Doc
    docs = {**docs, "sensors": Doc("sensors", "man", "print sensors information", (), "man")}
    r = run_for(docs, cfg, cands=[cand(docs, "sensors"), cand(docs, "find")])
    j = Scripted()
    P.invoke(r, j)
    out = P.critic(r, j)
    assert r.line == "" and r.signals["critic_p"] == 0.0     # compose will fire
    assert out[0].name == "sensors"                          # not replaced by a neighbour's line
    P.fill(r)
    assert r.line == "sensors"                               # nothing composed: the bare command


# ---------------------------------------------------------------- gate


def test_gate_assembles_the_shipped_form_and_classifies(docs, cfg):
    r = run_for(docs, cfg, cands=[cand(docs, "find")])
    r.line = "find . -size +100M"
    P.gate(r, None)
    assert r.assembled.comment_line == "find . -size +100M  # find big log files"
    assert r.verdict.tier == Tier.CLEAN


def test_gate_judge_may_raise_a_tier_but_a_judge_failure_is_not_a_shell_failure(docs, cfg):
    raised = run_for(docs, cfg, cands=[cand(docs, "find")])
    raised.line = "find . -size +100M"
    P.gate(raised, Scripted(noul=0.99))
    assert raised.verdict.tier >= Tier.NOTE

    broken = run_for(docs, cfg, cands=[cand(docs, "find")])
    broken.line = "find . -size +100M"
    P.gate(broken, Scripted(raises=True))
    assert broken.verdict.tier == Tier.CLEAN              # pattern verdict stands


def test_gate_falls_back_to_the_first_documented_line_when_no_stage_chose_one(docs, cfg):
    r = run_for(docs, cfg, cands=[cand(docs, "grep")])
    P.gate(r, None)
    assert r.assembled.line == "grep -r '{{pat}}' ."


def test_assembly_keeps_the_request_verbatim_and_lists_placeholders():
    a = assemble("find . -name '{{pattern}}' -size +{{size}}", "find big log files")
    assert a.comment_line == "find . -name '{{pattern}}' -size +{{size}}  # find big log files"
    assert a.placeholders == ["pattern", "size"] == placeholders(a.line)
    assert a.request == "find big log files"


# ---------------------------------------------------------------- the runner: order


def test_fill_then_gate_close_the_pipeline_and_nothing_comes_between():
    assert [name for name, _, _ in STAGES][-2:] == ["fill", "gate"]
    assert [name for name, _, _ in STAGES][0] == "retrieve"


def test_every_stage_is_traced_in_order(docs, cfg):
    a = ask(docs, cfg, Scripted(choices={"find": 5.0}))
    assert [n.kind for n in a.trace.nodes] == [name for name, _, _ in STAGES]
    assert a.candidate.name == "find" and a.assembled.line.startswith("find")


# ---------------------------------------------------------------- determinism


def test_same_query_index_and_answers_give_an_identical_trace(docs, cfg):
    a = ask(docs, cfg, Scripted(order_sensitive=True))
    b = ask(docs, cfg, Scripted(order_sensitive=True))
    assert strip_timings(a.trace.render()) == strip_timings(b.trace.render())
    assert a.assembled == b.assembled and a.candidate.name == b.candidate.name


# ---------------------------------------------------------------- degradation


def test_a_judge_that_fails_everywhere_still_gets_a_verdict(docs, cfg):
    a = ask(docs, cfg, Scripted(raises=True))
    assert a.trace.nodes[0].degraded and "JudgeUnavailable" in a.trace.nodes[0].detail
    assert a.assembled.line == "" and a.candidate.name == ""
    assert a.verdict is not None and not a.trace.nodes[-1].degraded


def test_a_failed_pick_keeps_the_tournament_order(docs, cfg):
    a = ask(docs, cfg, Scripted(choices={"du": 5.0, "find": 1.0}, fail_on="Prefer the program"))
    node = next(n for n in a.trace.nodes if n.kind == "pick")
    assert node.degraded and a.candidate.name == "du"
    assert a.assembled.line                                 # the user still gets a line


def test_a_failed_invoke_ships_the_first_documented_line(docs, cfg):
    a = ask(docs, cfg, Scripted(choices={"find": 5.0}, fail_on="documented way to run it"))
    assert next(n for n in a.trace.nodes if n.kind == "invoke").degraded
    assert a.assembled.line == fill_resolve("find . -name '{{pattern}}'", QUERY, Sources.live(),
                                            P.judge_pick(Scripted(choices={"find": 5.0}))).line


def test_a_stage_that_overruns_its_timeout_degrades_rather_than_hangs(docs, cfg):
    class Slow(Scripted):
        def noul(self, state, instructions, yes=None, no=None):
            time.sleep(0.4)
            return super().noul(state, instructions, yes, no)

    t0 = time.perf_counter()
    a = ask(docs, replace(cfg, node_timeout_s=0.05), Slow())
    assert time.perf_counter() - t0 < 3.0
    assert any(n.degraded and "Timeout" in n.detail for n in a.trace.nodes)


def test_a_timed_out_stage_cannot_write_to_the_run_it_missed(docs, cfg):
    """A thread that overran cannot be killed, so it must be writing somewhere dead.

    Regression: a slow `invoke` finishing after its timeout used to rewrite the line
    the gate had already assembled, and its notes landed on the gate's trace row.
    """
    class SlowInvoke(Scripted):
        def choice(self, state, instructions, options):
            if "invocations" in state:
                time.sleep(0.3)
            return super().choice(state, instructions, options)

    p = Pipeline(docs, cfg=replace(cfg, node_timeout_s=0.1),
                 judge_factory=lambda n: SlowInvoke(choices={"find": 5.0}, noul=0.9))
    a = p.run(QUERY)
    inv = next(n for n in a.trace.nodes if n.kind == "invoke")
    gate = a.trace.nodes[-1]
    assert inv.degraded
    assert "example=" not in gate.detail and "command=" not in gate.detail
    line_then = a.assembled.line
    time.sleep(0.4)                                              # let the late thread finish
    assert a.assembled.line == line_then


def test_the_budget_cap_degrades_a_paid_stage_and_never_kills_the_run(docs, cfg):
    j = Scripted(usd_per_call=0.01)
    j.paid = True
    a = ask(docs, replace(cfg, usd_cap=0.001), j)
    assert any("BudgetExceeded" in n.detail for n in a.trace.nodes if n.degraded)
    assert a.verdict is not None


def test_cost_and_latency_are_accounted_per_stage(docs, cfg):
    j = Scripted(usd_per_call=0.001)
    j.paid = True
    a = ask(docs, cfg, j)
    assert a.trace.total_usd == pytest.approx(sum(n.usd for n in a.trace.nodes))
    assert a.trace.total_usd == pytest.approx(j.cost_usd) and a.trace.total_usd > 0
    assert a.trace.total_ms >= max(n.ms for n in a.trace.nodes)


# ---------------------------------------------------------------- wiring


def test_config_judge_names_the_one_backend(docs, cfg):
    names = []
    a = Pipeline(docs, cfg=replace(cfg, judge="stub"),
                 judge_factory=lambda n: (names.append(n), Scripted())[1]).run(QUERY)
    assert names == ["stub"]
    assert {n.judge for n in a.trace.nodes if n.judge} == {"stub"}


def test_an_empty_judge_setting_means_jev(docs, cfg):
    assert Pipeline(docs, cfg=replace(cfg, judge="")).judge_name == "jev"


def test_answer_is_the_one_entry_point_and_returns_a_full_answer(docs, cfg):
    a = answer(QUERY, docs=docs, cfg=cfg, judge_factory=lambda n: Scripted(), cache=False)
    assert a.assembled.comment_line.endswith(f"# {QUERY}")
    assert a.candidate.name and a.verdict.tier in tuple(Tier)
    assert a.trace.render().startswith(repr(QUERY))


def test_empty_index_still_produces_an_answer(cfg):
    a = answer("qwertyuiop zxcvbnm", docs={}, cfg=cfg, judge_factory=lambda n: Scripted(), cache=False)
    assert a.assembled.line == "" and a.candidate.name == "" and a.verdict is not None


# ---------------------------------------------------------------- invoke prefetch


class _Memo(Scripted):
    """Scripted, plus the in-memory answer cache every real HTTP judge has."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.memo, self.misses = {}, []

    def choice(self, state, instructions, options):
        k = repr((state, instructions, list(options)))
        if k not in self.memo:
            self.misses.append(state.get("command"))
            self.memo[k] = super().choice(state, instructions, options)
        return self.memo[k]


def test_invoke_prefetches_exactly_what_the_critic_will_ask(docs, cfg):
    j = _Memo(choices={"find": 3.0, "du": 2.0, "grep": 1.0}, noul=0.1)
    r = run_for(docs, cfg, cands=[cand(docs, n) for n in ("find", "du", "grep", "rm")])
    P.invoke(r, j)
    before = list(j.misses)
    P.critic(r, j)
    assert j.misses == before                        # the critic asked nothing new
    assert Counter(before) == Counter(["find"])      # du and grep have one line each: nothing to ask
