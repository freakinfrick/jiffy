import json
import socket
from dataclasses import replace
import urllib.request

import pytest

from jiffy.bench import bench
from jiffy.bench.compare import diff, render, sign_test_p
from jiffy.bench.harness import BenchRefused, PAID_SAFE_LIMIT
from jiffy.bench.scoring import monotonic
from jiffy.graph.pipeline import PER_CHUNK


def run(docs, cases_file, cfg, factory, **kw):
    kw.setdefault("all_", True)
    return bench(cases_path=cases_file, cfg=cfg, docs=docs, judge_factory=factory, save=False, **kw)


def test_scores_list_and_str_gold(docs, cases_file, cfg, stub_factory):
    res = run(docs, cases_file, cfg, stub_factory)
    rows = res["cases"]
    assert rows[0]["gold"] == ["alpha"] and rows[1]["gold"] == ["beta", "gamma"]
    assert rows[1]["hit"] == (rows[1]["chosen"] in ("beta", "gamma"))
    assert res["top1_hits"] == sum(r["hit"] for r in rows)
    assert res["top1"] == res["top1_hits"] / 3


def test_recall_is_monotonically_non_increasing_down_the_stages(docs, cases_file, cfg, stub_factory):
    res = run(docs, cases_file, cfg, stub_factory)
    chain = [s["recall"] for s in res["stages"]] + [res["top1"]]
    assert monotonic(chain), chain
    assert res["recall_monotonic"]
    by = {s["node"]: s for s in res["stages"]}
    assert by["retrieve"]["m_median"] == PER_CHUNK < len(docs)       # the tournament really narrowed it
    for r in res["cases"]:                                          # and per case, not just on average
        seq = [r["stage_hit"][s["node"]] for s in res["stages"]] + [r["hit"]]
        assert monotonic([float(x) for x in seq]), (r["q"], seq)


def test_calls_and_degradation_counted_per_stage(docs, cases_file, cfg, stub_factory):
    res = run(docs, cases_file, cfg, stub_factory)
    assert res["calls_per_node"]["retrieve"] == 3            # one chunk per case: 42 commands < CHUNK
    assert res["degraded_total"] == sum(res["degraded_per_node"].values()) == 0
    assert res["latency_ms"]["p95"] >= res["latency_ms"]["p50"] > 0


def test_dry_makes_zero_network_calls(docs, cases_file, cfg, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched during --dry")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    def no_factory(name):
        raise AssertionError(f"dry must not instantiate a real judge ({name})")
    res = run(docs, cases_file, replace(cfg, judge="jev"), no_factory, dry=True)
    assert res["mode"] == "dry" and res["degraded_total"] == 0
    assert sum(res["calls_per_node"].values()) > 0
    assert res["usd_estimated"] and res["usd"] > 0


def test_dry_writes_no_result_file(docs, cases_file, cfg, tmp_path, monkeypatch):
    import jiffy.bench.harness as h
    monkeypatch.setattr(h, "RESULTS_DIR", tmp_path / "res")
    bench(cases_path=cases_file, cfg=replace(cfg, judge="jev"), docs=docs, dry=True, all_=True)
    assert not (tmp_path / "res").exists()


def test_a_paid_judge_is_refused_without_all(docs, cfg, tmp_path, stub_factory):
    many = tmp_path / "many.json"
    many.write_text(json.dumps([{"q": f"list files folder variant{i}", "cmd": "alpha"}
                                for i in range(PAID_SAFE_LIMIT + 5)]))
    with pytest.raises(BenchRefused, match="--all"):
        bench(cases_path=many, limit=PAID_SAFE_LIMIT + 5, cfg=replace(cfg, judge="jev"),
              docs=docs, judge_factory=stub_factory, save=False)


def test_a_paid_judge_is_refused_when_the_estimate_exceeds_the_cap(docs, cases_file, cfg, stub_factory):
    with pytest.raises(BenchRefused, match="usd_cap"):
        run(docs, cases_file, replace(cfg, judge="jev", usd_cap=1e-9), stub_factory)


def test_default_limit_is_small(docs, tmp_path, cfg, stub_factory):
    many = tmp_path / "many.json"
    many.write_text(json.dumps([{"q": "list files", "cmd": "alpha"}] * 30))
    res = bench(cases_path=many, cfg=cfg, docs=docs, judge_factory=stub_factory, save=False)
    assert res["n_cases"] == 5


def test_compare_reports_per_case_wins_and_losses():
    def r(label, hits):
        return {"label": label, "top1": sum(hits) / len(hits), "usd": 0.0,
                "latency_ms": {"p50": 1, "p95": 2},
                "cases": [{"q": f"q{i}", "gold": ["x"], "chosen": "x" if h else "y", "hit": h}
                          for i, h in enumerate(hits)]}
    d = diff(r("A", [1, 1, 0, 0, 1]), r("B", [1, 0, 1, 0, 1]))
    assert [x["q"] for x in d["b_only"]] == ["q2"] and [x["q"] for x in d["a_only"]] == ["q1"]
    assert d["both"] == 2 and d["neither"] == 1 and d["delta"] == 0
    assert "only B got right" in render(d)
    assert sign_test_p(0, 0) == 1.0 and sign_test_p(5, 0) == pytest.approx(0.0625)
    with pytest.raises(ValueError):
        diff(r("A", [1]), r("B", [1, 0]))


def test_counting_counts_a_repeated_request_once():
    # the real adapters answer repeats from memory; invoke prefetches the critic's questions
    from jiffy.bench.harness import Counting
    from jiffy.judge import get
    c = Counting(get("stub"), "jev", dry=True)
    for _ in range(3):
        c.choice({"a": 1}, "q", ["x", "y"])
        c.noul({"a": 1}, "q")
    c.noul({"a": 2}, "q")
    assert c.calls == 3
    one = Counting(get("stub"), "jev", dry=True)
    one.choice({"a": 1}, "q", ["x", "y"])
    one.noul({"a": 1}, "q")
    one.noul({"a": 2}, "q")
    assert c.est_usd == one.est_usd
