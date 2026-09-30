"""Run the pipeline over the cases and measure it.

The bench drives the real pipeline. It subclasses it only to snapshot the candidate
list after each stage (recall per stage: a candidate dropped by the tournament can
never be recovered by a later judge) and to meter judge calls per stage. `--dry`
swaps the judge for a deterministic offline stub that only *counts* — the prompts
it is shown are the real ones, so the token bill is the real one, minus whatever
the critic and compose would decide differently with a real judge.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from jiffy.config import Config, load as load_config
from jiffy.graph import api as graph_api
from jiffy.graph.pipeline import DEFAULT_JUDGE, STAGES, Pipeline
from jiffy.judge import get as get_judge
from jiffy.judge._http import PRICE_PER_MTOK, Ledger
from jiffy.judge.base import JudgeAdapter
from jiffy.types import Doc

from .cases import LABELS_PATH, RESULTS_DIR, gold, load_cases
from .grade import LINE_THRESHOLD, grade_line, load_labels, unfilled
from .scoring import contains, hit, monotonic, percentile, rate

CHARS_PER_TOKEN = 3.5        # JSON is token-dense; err on over-estimating the bill
RETRIEVE_CUTS = (1, 5, 20, 120)
DEFAULT_LIMIT = 5            # an accidental invocation is cheap
PAID_SAFE_LIMIT = 10         # above this a paid judge needs --all
PAID_JUDGES = {"jev"}
LINE_GRADER = "jev"          # `--line` grades every shipped line with this judge


# ---------------------------------------------------------------- metering


class Counting(JudgeAdapter):
    """Wraps a judge; counts calls. In dry mode `inner` is a stub and cost is *estimated*
    from the size of the real request, priced as the paid API would price it."""

    def __init__(self, inner: JudgeAdapter, name: str, dry: bool = False) -> None:
        self.inner, self._label, self.dry = inner, name, dry
        self.paid = bool(getattr(inner, "paid", False)) or (dry and name in PAID_JUDGES)
        self.calls = 0
        self.est_usd = 0.0
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._label

    @property
    def cost_usd(self) -> float:            # type: ignore[override]
        return self.inner.cost_usd + self.est_usd

    def _tick(self, state: Any, instructions: str, *rest: Any) -> None:
        # Count each distinct request once. The real adapters answer a repeat from
        # their in-memory cache without the network, and invoke deliberately asks
        # the critic's questions early (Invoke.prefetch); counting the critic's
        # repeats would double both the call count and the dry cost estimate.
        k = json.dumps([state, instructions, *rest], sort_keys=True, ensure_ascii=False, default=str)
        with self._lock:
            if k in self._seen:
                return
            self._seen.add(k)
            self.calls += 1
            if self.dry and self.paid:
                chars = len(json.dumps(state, ensure_ascii=False, default=str)) + len(instructions)
                self.est_usd += chars / CHARS_PER_TOKEN * PRICE_PER_MTOK / 1e6

    def choice(self, state, instructions, options):
        self._tick(state, instructions, list(options))
        return self.inner.choice(state, instructions, options)

    def noul(self, state, instructions, yes=None, no=None):
        self._tick(state, instructions, yes, no)
        return self.inner.noul(state, instructions, yes, no)

    def batch(self, jobs: Iterable[Callable[[], Any]]) -> list:
        return self.inner.batch(jobs)


class BenchPipeline(Pipeline):
    """Pipeline + per-stage candidate snapshots + per-stage call counts."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.stage_log: list[tuple[str, list[str]]] = []
        self.calls_log: dict[str, int] = {}

    def _calls(self) -> int:
        return getattr(self._judge, "calls", 0)

    def _stage(self, name, fn, judged, r, cancelled):
        before = self._calls()
        out = super()._stage(name, fn, judged, r, cancelled)
        self.calls_log[name] = self.calls_log.get(name, 0) + self._calls() - before
        self.stage_log.append((name, [c.name for c in r.cands]))
        return out

    def reset_log(self) -> None:
        self.stage_log, self.calls_log = [], {}


def judge_name(cfg: Config) -> str:
    return (cfg.judge or "").strip() or DEFAULT_JUDGE


def is_paid(cfg: Config) -> bool:
    return judge_name(cfg) in PAID_JUDGES


# ---------------------------------------------------------------- one run


def run_cases(cases: list[dict], docs: dict[str, Doc], cfg: Config,
              dry: bool = False, judge_factory: Callable[[str], JudgeAdapter] | None = None,
              progress: Callable[[int, int, float], None] | None = None,
              line: bool = False, labels: Path | str | None = LABELS_PATH) -> dict:
    """Score the pipeline over `cases`. Returns the result dict (see `bench`).

    `line` also grades every shipped line: by its label in `labels` when one exists,
    else with `grade.grade_line` and its own judge, metered apart from the pipeline so
    `usd` stays the cost of answering alone.
    """
    if dry:
        from jiffy.judge.backends import Stub
        factory = lambda name: Counting(Stub(), name, dry=True)    # noqa: E731
        cfg = replace(cfg, usd_cap=1e9)               # dry never trips the cap, never dials out
    else:
        base = judge_factory or (lambda name: get_judge(name, config=cfg))
        factory = lambda name: Counting(base(name), name)                        # noqa: E731
    ex = BenchPipeline(docs, cfg=cfg, judge_factory=factory)
    grader = factory(LINE_GRADER) if line else None

    rows: list[dict] = []
    calls: dict[str, int] = {}
    degraded: dict[str, int] = {}
    t_start = time.perf_counter()
    for i, case in enumerate(cases):
        ex.reset_log()
        t0 = time.perf_counter()
        ans = ex.run(case["q"])
        ms = (time.perf_counter() - t0) * 1000.0
        stages = ex.stage_log
        stage_hit = {nid: contains(case, names) for nid, names in stages}
        stage_n = {nid: len(names) for nid, names in stages}
        retrieved = stages[0][1] if stages else []
        cut_hit = {f"@{m}": contains(case, retrieved[:m]) for m in RETRIEVE_CUTS}
        for nid, n in ex.calls_log.items():
            calls[nid] = calls.get(nid, 0) + n
        deg = [t.node_id for t in ans.trace.nodes if t.degraded]
        for nid in deg:
            degraded[nid] = degraded.get(nid, 0) + 1
        shipped = ans.assembled.line if ans.assembled else ""
        rows.append({
            "q": case["q"], "gold": gold(case), "chosen": ans.candidate.name,
            "type": case.get("type", ""), "split": case.get("split", "dev"),
            "hit": hit(case, ans.candidate.name), "line": shipped, "unfilled": unfilled(shipped),
            "ms": round(ms, 1),
            "usd": ans.trace.total_usd, "stage_hit": stage_hit, "stage_n": stage_n,
            "retrieve_cut_hit": cut_hit, "degraded": deg,
        })
        if progress:
            el = time.perf_counter() - t_start
            progress(i + 1, len(cases), el / (i + 1) * (len(cases) - i - 1))

    if grader is not None:
        _grade_rows(grader, rows, load_labels(labels))

    order = [name for name, _, _ in STAGES]
    stage_rows = []
    for nid in order:
        ns = [r["stage_n"][nid] for r in rows if nid in r["stage_n"]]
        stage_rows.append({
            "node": nid, "kind": nid,
            "m_median": percentile(ns, 50), "recall": rate([r["stage_hit"].get(nid, False) for r in rows]),
        })
    top1 = rate([r["hit"] for r in rows])
    chain = [s["recall"] for s in stage_rows] + [top1]
    lat = [r["ms"] for r in rows]
    total_usd = sum(r["usd"] for r in rows)
    res = {
        "graph": "pipeline", "date": _dt.date.today().isoformat(), "mode": "dry" if dry else "live",
        "n_cases": len(rows), "judges": [judge_name(cfg)],
        "top1": top1, "top1_hits": sum(r["hit"] for r in rows),
        "stages": stage_rows,
        "retrieve_recall": {f"@{m}": rate([r["retrieve_cut_hit"][f"@{m}"] for r in rows]) for m in RETRIEVE_CUTS},
        "recall_monotonic": monotonic(chain),
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95),
                       "mean": sum(lat) / len(lat) if lat else 0.0},
        "usd": total_usd,     # scalar: machine consumers read res["usd"] as a float
        "usd_per_case": total_usd / len(rows) if rows else 0.0,
        "usd_estimated": dry,
        "calls_per_node": calls, "degraded_per_node": degraded,
        "degraded_total": sum(degraded.values()),
        "cases": rows,
    }
    res["line"] = line_summary(rows, grader)
    return res


def _grade_rows(grader: JudgeAdapter, rows: list[dict], labels: dict[tuple[str, str], int]) -> None:
    """A labelled line takes its label; the rest go to the judge in one batch.
    A failed grade is recorded as None, never as a pass."""
    for r in rows:
        if (r["q"], r["line"]) in labels:
            r["line_p"] = float(labels[(r["q"], r["line"])])
            r["line_ok"], r["graded_by"] = r["line_p"] >= LINE_THRESHOLD, "label"
    todo = [r for r in rows if "graded_by" not in r]
    jobs = [(lambda r=r: grade_line(grader, r["q"], r["line"])) for r in todo]
    for r, p in zip(todo, grader.batch(jobs)):
        ok = isinstance(p, (int, float))
        r["line_p"] = round(float(p), 3) if ok else None
        r["line_ok"], r["graded_by"] = bool(ok and p >= LINE_THRESHOLD), "judge"


def _line_block(rows: list[dict]) -> dict:
    graded = [r for r in rows if r.get("line_p") is not None]
    return {"n": len(rows), "graded": len(graded),
            "ok_n": sum(r["line_ok"] for r in graded),
            "ok_rate": rate([r["line_ok"] for r in graded]),
            "unfilled_rate": rate([r["unfilled"] > 0 for r in rows])}


def line_summary(rows: list[dict], grader: JudgeAdapter | None) -> dict | None:
    """`line_ok` overall, per split (dev / heldout) and per failure type."""
    if grader is None:
        return None
    out = _line_block(rows)
    out["threshold"] = LINE_THRESHOLD
    out["usd"] = grader.cost_usd
    out["failed"] = sum(1 for r in rows if r.get("line_p") is None)
    out["by_label"] = sum(1 for r in rows if r.get("graded_by") == "label")
    for key in ("split", "type"):
        groups = sorted({r[key] for r in rows if r[key]})
        out[f"by_{key}"] = {g: _line_block([r for r in rows if r[key] == g]) for g in groups}
    return out


# ---------------------------------------------------------------- entry point


class BenchRefused(RuntimeError):
    """The invocation would spend money the user has not explicitly asked to spend."""


def select_cases(cases: list[dict], limit: int | None, all_: bool) -> list[dict]:
    return cases if all_ else cases[: (DEFAULT_LIMIT if limit is None else limit)]


def bench(cases_path: Path | str | Sequence[Path | str] | None = None,
          limit: int | None = None,
          all_: bool = False, dry: bool = False, usd_cap: float | None = None,
          cfg: Config | None = None,
          docs: dict[str, Doc] | None = None,
          judge_factory: Callable[[str], JudgeAdapter] | None = None,
          progress: Callable[[int, int, float], None] | None = None,
          save: bool = True, line: bool = False, split: str | None = None,
          labels: Path | str | None = LABELS_PATH) -> dict:
    """`jiffy bench`. Live runs with a paid judge are refused unless the user passed
    --all (or a limit <= PAID_SAFE_LIMIT) and the dry estimate fits the cap."""
    cfg = cfg or load_config(write_default=False)
    if usd_cap is not None:
        cfg = replace(cfg, usd_cap=float(usd_cap))
    docs = docs if docs is not None else graph_api.corpus(cfg)
    paths = [cases_path] if cases_path is None or isinstance(cases_path, (str, Path)) else list(cases_path)
    all_cases = [c for p in paths for c in load_cases(p)]
    if split:
        all_cases = [c for c in all_cases if c.get("split", "dev") == split]
    cases = select_cases(all_cases, limit, all_)
    paid = is_paid(cfg)
    spends = paid or (line and LINE_GRADER in PAID_JUDGES)

    if not dry and spends:
        if len(cases) > PAID_SAFE_LIMIT and not all_:
            raise BenchRefused(
                f"the {judge_name(cfg)!r} judge spends money; {len(cases)} cases needs an explicit --all "
                f"(or --limit <= {PAID_SAFE_LIMIT}). Try --dry first.")
        est = _dry_usd(cases, docs, cfg, line)
        # The cap is per day, not per run: compare against what is LEFT of today's.
        spent = Ledger().spent_today()
        if spent + est > cfg.usd_cap:
            raise BenchRefused(
                f"dry estimate ${est:.4f} on top of ${spent:.4f} already spent today exceeds jiffy's daily usd_cap "
                f"${cfg.usd_cap:.4f}; stages would degrade mid-run and poison the result. "
                f"Re-run with --usd-cap {(spent + est) * 1.25:.2f} to allow it.")

    res = run_cases(cases, docs, cfg, dry=dry, judge_factory=judge_factory, progress=progress,
                    line=line, labels=labels)
    res["paid"] = paid
    if dry and len(cases) < len(all_cases):
        res["estimate_full_usd"] = _dry_usd(all_cases, docs, cfg, line)
    elif dry:
        res["estimate_full_usd"] = res["usd"] + (res["line"]["usd"] if res["line"] else 0.0)
    if save and not dry:
        res["path"] = str(save_result(res))
    return res


def _dry_usd(cases: list[dict], docs: dict[str, Doc], cfg: Config, line: bool) -> float:
    r = run_cases(cases, docs, cfg, dry=True, line=line)
    return r["usd"] + (r["line"]["usd"] if r["line"] else 0.0)


def save_result(res: dict, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    tag = "-line" if res.get("line") else ""
    p = results_dir / f"{res['graph']}-{res['date']}{tag}.json"
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    return p
