"""The one pipeline: tournament -> pick -> invoke -> critic -> compose -> fill -> gate.

Every stage is a plain function of (Run, judge). `Pipeline.run` times, meters and
degrades each one and polls cancellation between them. Three promises, in the
order they matter:

1. DEGRADE. A stage that times out, errors, or hits JudgeUnavailable /
   BudgetExceeded keeps what the stage before it produced, and its trace says
   `degraded`. Only retrieval has nothing to fall back to: no candidates, no line.
2. DETERMINISM. Same query + index + judge answers => same trace, modulo timings.
3. The gate is terminal and NEVER skippable. It runs after fill, on the line that
   will actually run, and a judge failure there leaves the pattern verdict standing.

Judge state shapes and instruction wordings for pick/invoke/critic/gate are lifted
verbatim from `~/jev-probes/p12_cli.py`; they won a measured sweep. The tournament
wording is the one the phase-3 probe measured (docs/overhaul-plan.md).
"""
from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from jiffy import gate as gate_pkg
from jiffy import judge as judge_pkg
from jiffy.config import Config, load as load_config
from jiffy.corpus.index import installed
from jiffy.fill import Ask, Pick, Sources, resolve as fill_resolve
from jiffy.fill.sources import MAX_OPTIONS
from jiffy.judge.base import JudgeAdapter
from jiffy.judge.errors import BudgetExceeded, JudgeUnavailable
from jiffy.types import Answer, Assembled, Candidate, Doc, Example, GateVerdict, NodeTrace, Trace

from . import compose as comp
from .assemble import assemble
from .cancel import NEVER, check

__all__ = ["Pipeline", "Run", "STAGES", "EMPTY_CANDIDATE", "DEFAULT_JUDGE"]

DEFAULT_JUDGE = "jev"
CHUNK = 150          # Jev choice takes at most 150 options
PER_CHUNK = 3        # finalists each chunk sends to the final pick
CRITIC_THRESHOLD = 0.5
CRITIC_DEPTH = 2
COMPOSE_K, COMPOSE_LISTWISE, COMPOSE_MAX_OPTS = 16, 6, 4

EMPTY_DOC = Doc("", "", "", (), "")
EMPTY_CANDIDATE = Candidate("", EMPTY_DOC)


@dataclass
class Run:
    """One query's working state. Stages read it and write the fields below."""

    query: str
    docs: dict[str, Doc]
    cfg: Any
    cands: list[Candidate] = field(default_factory=list)
    line: str = ""
    line_does: str = ""
    fills: tuple = ()
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)   # compose's closed blanks
    signals: dict[str, float] = field(default_factory=dict)   # invocation_p, critic_p, compose_p
    detail: dict[str, Any] = field(default_factory=dict)      # current stage's notes
    assembled: Assembled | None = None
    verdict: GateVerdict | None = None

    def note(self, **kw: Any) -> None:
        self.detail.update(kw)


def _examples(d: Doc) -> list[Example]:
    return list(d.examples)


def _with(c: Candidate, stage: str, value: float) -> Candidate:
    return replace(c, scores={**c.scores, stage: float(value)})


def _collect(results: list, n: int) -> tuple[list[Any], int]:
    """batch() contract: an exception object sits in the slot of a failed job."""
    ok = [None if isinstance(r, Exception) else r for r in results]
    failed = sum(r is None for r in ok)
    if n and failed == n:
        first = next(r for r in results if isinstance(r, Exception))
        raise JudgeUnavailable(f"all {n} batched jobs failed: {type(first).__name__}: {first}"[:300])
    return ok, failed


# ---------------------------------------------------------------- retrieve: the tournament


TOURNEY_Q = "Which shell command would a person run to do what they asked?"


def _tourney_state(query: str, entries: Sequence[str]) -> dict:
    return {"query": query, "candidates": {f"c{i}": e for i, e in enumerate(entries)}}


def retrieve(r: Run, j: JudgeAdapter) -> list[Candidate]:
    """Every indexed command, `name: desc`, in chunks of CHUNK; one batch; top PER_CHUNK each.

    Finalists come back ranked by their chunk probability. The final pick is its own stage.
    """
    names = sorted(r.docs)
    if not names:
        return []
    entries = [f"{n}: {r.docs[n].desc}" for n in names]
    chunks = [range(i, min(i + CHUNK, len(names))) for i in range(0, len(names), CHUNK)]

    def job(idx: range) -> dict[str, float]:
        return j.choice(_tourney_state(r.query, [entries[i] for i in idx]), TOURNEY_Q,
                        [f"c{k}" for k in range(len(idx))])

    results, failed = _collect(j.batch([(lambda idx=idx: job(idx)) for idx in chunks]), len(chunks))
    wins: dict[str, float] = {}
    for idx, probs in zip(chunks, results):
        if probs is None:
            continue
        top = sorted(range(len(idx)), key=lambda k: -probs.get(f"c{k}", 0.0))[:PER_CHUNK]
        for k in top:
            wins[names[idx[k]]] = float(probs.get(f"c{k}", 0.0))
    order = sorted(wins, key=lambda n: (-wins[n], n))
    r.note(indexed=len(names), chunks=len(chunks), failed=failed, top=order[:3])
    return [_with(Candidate(n, r.docs[n]), "retrieve", wins[n]) for n in order]


# ---------------------------------------------------------------- pick


PICK_INSTRUCTIONS = (
    "A person describes, in their own words, a task they want to do in a Linux shell. Each entry in "
    "`commands` is one command-line program: `does` is what it is for, `uses` are things people do with it. "
    "Which entry is the program that does what `user_request` asks for? Prefer the program whose ordinary, "
    "documented purpose is this task over one that could be bent into doing it.")


def _pick_state(query: str, cands: Sequence[Candidate]) -> dict:
    return {"user_request": query, "commands": {
        f"c{i}": {"name": c.name, "does": c.doc.desc,
                  "uses": [e.does for e in _examples(c.doc)[:3]]}
        for i, c in enumerate(cands)}}


def pick(r: Run, j: JudgeAdapter) -> list[Candidate]:
    """One listwise choice over the finalists, with what each is for and how it is used."""
    if not r.cands:
        return []
    keys = [f"c{i}" for i in range(len(r.cands))]
    probs = j.choice(_pick_state(r.query, r.cands), PICK_INSTRUCTIONS, keys)
    scored = [_with(c, "pick", probs.get(k, 0.0)) for k, c in zip(keys, r.cands)]
    ranked = sorted(scored, key=lambda c: -c.scores["pick"])
    r.note(top=[(c.name, round(c.scores["pick"], 3)) for c in ranked[:3]])
    return ranked


# ---------------------------------------------------------------- invoke


def _invoke_state(query: str, d: Doc) -> dict:
    return {"user_request": query, "command": d.name,
            "invocations": {f"e{i}": {"does": e.does, "line": e.line} for i, e in enumerate(_examples(d))}}


def _invoke_instructions(name: str) -> str:
    return (f"A person wants to do what `user_request` says, using `{name}`. Each entry in `invocations` is one "
            "documented way to run it. Which entry does exactly what the person asked for — including any restriction "
            "they stated (only files, only directories, only this size, recursively, sorted, hidden ones too)?")


def _best_example(j: JudgeAdapter, query: str, d: Doc) -> tuple[int, float]:
    ex = _examples(d)
    if len(ex) <= 1:
        return 0, 1.0
    keys = [f"e{i}" for i in range(len(ex))]
    probs = j.choice(_invoke_state(query, d), _invoke_instructions(d.name), keys)
    best = max(range(len(ex)), key=lambda i: probs.get(f"e{i}", 0.0))
    return best, float(probs.get(f"e{best}", 0.0))


def invoke(r: Run, j: JudgeAdapter) -> list[Candidate]:
    """Listwise choice over the winner's documented examples -> the line.

    The critic will ask the same question of the next CRITIC_DEPTH candidates; asking
    them here, in the same batch, costs no extra round trip and leaves the answers in
    the judge's in-memory cache. A winner with no examples leaves the line empty:
    the critic then scores it 0 and compose builds one from its option table.
    """
    if not r.cands:
        return []
    top = r.cands[0]
    ahead = [c.doc for c in r.cands[1:1 + CRITIC_DEPTH]]
    ex = _examples(top.doc)
    if not ex:
        if ahead:
            j.batch([(lambda d=d: _best_example(j, r.query, d)) for d in ahead])
        r.note(command=top.name, examples=0)
        return r.cands
    first = j.batch([(lambda d=d: _best_example(j, r.query, d)) for d in [top.doc, *ahead]])[0]
    if isinstance(first, BaseException):
        raise first
    i, p = first
    r.line, r.line_does = ex[i].line, ex[i].does
    r.signals["invocation_p"] = p
    r.note(command=top.name, example=i, p=round(p, 3), line=r.line)
    return r.cands


# ---------------------------------------------------------------- critic


CRITIC_INSTRUCTIONS = (
    "A person wants to do what `user_request` says. Does `invocation` do exactly that, including any "
    "restriction they stated (only files, only lines, only this size, recursively, sorted, hidden too)?")
CRITIC_YES = "It does the stated task under the stated restriction."
CRITIC_NO = "It does a different thing with the same command, or ignores the restriction."


def _critic_state(query: str, name: str, e: Example) -> dict:
    return {"user_request": query, "command": name, "invocation": {"does": e.does, "line": e.line}}


def critic(r: Run, j: JudgeAdapter) -> list[Candidate]:
    """Noul over the shipped line; below threshold, fall back to the next candidate.

    Grades the incumbent and the next CRITIC_DEPTH candidates' own best invocations in
    one pass and ships the first that clears the threshold. A winner with no
    documented line is not replaced by a neighbour's: it scores 0 and goes to compose.
    """
    if not r.cands:
        return []
    if not _examples(r.cands[0].doc):
        r.signals["critic_p"] = 0.0
        r.note(verdict="no documented line", to="compose")
        return r.cands
    walk = r.cands[: 1 + CRITIC_DEPTH]
    lines: list[Example | None] = [Example(r.line_does, r.line) if r.line else _examples(walk[0].doc)[0]]
    tail = walk[1:]
    if tail:
        picked, _ = _collect(j.batch([(lambda d=c.doc: _best_example(j, r.query, d)) for c in tail]), len(tail))
        for c, pk in zip(tail, picked):
            ex = _examples(c.doc)
            lines.append(None if not ex else ex[pk[0]] if pk is not None else ex[0])
    pairs = [(c, e) for c, e in zip(walk, lines) if e is not None]
    jobs = [(lambda s=_critic_state(r.query, c.name, e): j.noul(
        s, CRITIC_INSTRUCTIONS, yes=CRITIC_YES, no=CRITIC_NO)) for c, e in pairs]
    probs, failed = _collect(j.batch(jobs), len(jobs))

    clearing = [i for i, p in enumerate(probs) if p is not None and float(p) >= CRITIC_THRESHOLD]
    # Of the lines that clear the bar, the first one that can run here wins:
    # `ipinfo myip` (0.53, not installed) must not beat `curl -s ifconfig.me`.
    # Nothing clearing: the incumbent stands, flagged. Shipping the highest-graded
    # fallback instead was measured (2026-09-21, 160 cases): top-1 fell 0.719 -> 0.650.
    chosen = next((i for i in clearing if installed(pairs[i][0].name)), clearing[0]) if clearing else 0
    c, e = pairs[chosen]
    r.line, r.line_does = e.line, e.does
    p = probs[chosen]
    r.signals["critic_p"] = 0.0 if p is None else float(p)
    out = list(r.cands)
    if chosen:
        out = [c] + [x for x in r.cands if x.name != c.name]
    out = [_with(x, "critic", r.signals["critic_p"] if x.name == c.name else 0.0) for x in out]
    r.note(failed=failed, installed=installed(c.name), graded=[(pc.name, round(float(q), 3) if q is not None else None)
                                  for (pc, _), q in zip(pairs, probs)],
           fell_back=bool(chosen), shipped=c.name, line=r.line,
           below_threshold=r.signals["critic_p"] < CRITIC_THRESHOLD)
    return out


# ---------------------------------------------------------------- compose


COMPOSE_LIST_INSTRUCTIONS = (
    "A person wants to do what `user_request` says, using `command`. Each entry in `options` is one of its "
    "documented options. Which option is most needed to do exactly what they asked?")
COMPOSE_OPTION_INSTRUCTIONS = (
    "A person wants to do what `user_request` says, using `command`. Does the request ask for what `option` "
    "does? Yes only if leaving it out would miss something they asked for.")
COMPOSE_YES = "The request asks for this; without it the command misses part of what they want."
COMPOSE_NO = "The request does not ask for this, or it would change the result in a way they did not ask for."


def _compose_one(j: JudgeAdapter, query: str, c: Candidate, spec) -> dict:
    usable = [o for o in spec.options if comp.usable(o)]
    short = comp.relevant(spec, query, COMPOSE_K)
    if usable and COMPOSE_LISTWISE > 0:
        keys = [f"o{i}" for i in range(len(usable))]
        state = {"user_request": query, "command": c.name,
                 "options": {k: f"{comp.spell(o)} -- {o.desc[:100]}" for k, o in zip(keys, usable)}}
        probs = j.choice(state, COMPOSE_LIST_INSTRUCTIONS, keys)
        ranked = sorted(range(len(usable)), key=lambda i: -probs.get(keys[i], 0.0))
        short += [usable[i] for i in ranked[:COMPOSE_LISTWISE] if usable[i] not in short]
    jobs = [(lambda o=o: j.noul({"user_request": query, "command": c.name,
                                 "option": {"flag": comp.spell(o), "does": o.desc}},
                                COMPOSE_OPTION_INSTRUCTIONS, yes=COMPOSE_YES, no=COMPOSE_NO)) for o in short]
    ps = j.batch(jobs) if jobs else []
    yes = sorted(((float(p), i) for i, p in enumerate(ps) if isinstance(p, (int, float)) and p >= 0.5),
                 reverse=True)
    kept = comp.prune([short[i] for _, i in yes])[:COMPOSE_MAX_OPTS]
    chosen = [o for o in short if o in kept]                              # keep short-list order
    chosen = comp.with_leaders(spec, chosen)
    line = comp.build_line(c.name, spec, chosen, query)
    choices = comp.closed(chosen)
    does = comp.describe(c.doc.desc, chosen)
    # Graded as it will run: the fill stage asks the same questions of the same line,
    # so its picks come back from the judge's cache.
    filled = fill_resolve(line, query, Sources.live(history=True), judge_pick(j), choices).line
    p = float(j.noul({"user_request": query, "command": c.name, "invocation": {"does": does, "line": filled}},
                     CRITIC_INSTRUCTIONS, yes=CRITIC_YES, no=CRITIC_NO))
    return {"cand": c, "line": line, "choices": choices, "filled": filled, "does": does, "p": p, "asked": len(short),
            "chosen": [o.short for o in chosen]}


def compose(r: Run, j: JudgeAdapter) -> list[Candidate]:
    """When no documented line clears the critic, build one from the man page's option table.

    Fires only below the critic threshold, for the first CRITIC_DEPTH candidates that are
    installed and have a table. Code short-lists options by word overlap and one listwise
    call adds the best COMPOSE_LISTWISE by meaning; the judge then says yes/no per option,
    at most COMPOSE_MAX_OPTS are kept, and `compose.build_line` writes the line. The
    critic's own question grades it, and it ships only if it beats the incumbent.
    """
    from jiffy.corpus import options as options_pkg
    incumbent = r.signals.get("critic_p")
    if not r.cands or incumbent is None or incumbent >= CRITIC_THRESHOLD:
        r.note(fired=False, critic_p=incumbent)
        return r.cands
    walk = []
    for c in r.cands[: CRITIC_DEPTH + 3]:
        if len(walk) >= CRITIC_DEPTH:
            break
        if shutil.which(c.name.split()[0]):
            spec = options_pkg.spec(c.name, r.cfg.corpus_dir)
            if spec is not None:
                walk.append((c, spec))
    if not walk:
        r.note(fired=True, composed=0, reason="no installed candidate with a man page")
        return r.cands
    results, failed = _collect(j.batch([(lambda c=c, s=s: _compose_one(j, r.query, c, s)) for c, s in walk]),
                               len(walk))
    tried = [x for x in results if x is not None]
    r.note(fired=True, failed=failed,
           tried=[(x["cand"].name, x["filled"], round(x["p"], 3), x["chosen"], x["asked"]) for x in tried])
    best = max(tried, key=lambda x: x["p"], default=None)
    if best is None or (best["p"] <= incumbent and r.line):
        r.note(shipped=False, incumbent_p=round(incumbent, 3))
        return r.cands
    r.line, r.line_does, r.choices = best["line"], best["does"], best["choices"]
    r.signals["compose_p"] = best["p"]
    r.note(shipped=True, line=best["filled"], p=round(best["p"], 3), incumbent_p=round(incumbent, 3))
    c = best["cand"]
    out = [c] + [x for x in r.cands if x.name != c.name]
    return [_with(x, "compose", best["p"] if x.name == c.name else 0.0) for x in out]


# ---------------------------------------------------------------- fill


def _default_line(r: Run) -> tuple[str, str]:
    """The line when nothing chose one: the winner's first example, else its bare name.

    Fill and gate both need it and must agree, so the gate grades what will run.
    """
    if not r.cands:
        return "", ""
    top = r.cands[0]
    ex = _examples(top.doc)
    return (ex[0].line, ex[0].does) if ex else (top.name, top.doc.desc)


FILL_INSTRUCTIONS = (
    "A person typed `user_request`; `line` is the command that will do it. `blank` is one `{{...}}` in `line` "
    "still to fill. Each entry in `values` is a value found in the request or on this machine. Which value did "
    "the person state or clearly mean for this blank? Choose `none` when the request does not say what goes there.")
FILL_NONE = "leave the blank for the person to fill in"


def _fill_state(query: str, line: str, ask: Ask) -> dict:
    token, cands = ask
    values = {f"v{i}": f"{c.value}  ({c.why})" if c.why else c.value for i, c in enumerate(cands[:MAX_OPTIONS])}
    return {"user_request": query, "line": line, "blank": "{{" + token.raw + "}}", "values": {**values, "none": FILL_NONE}}


def judge_pick(j: JudgeAdapter) -> Pick:
    """One batch, one choice per token: which proposal, or `none`. A failed choice keeps code's."""
    def pick(query: str, line: str, asks) -> list[int | None]:
        keys = [[f"v{i}" for i in range(min(len(c), MAX_OPTIONS))] + ["none"] for _, c in asks]
        jobs = [(lambda a=a, k=k: j.choice(_fill_state(query, line, a), FILL_INSTRUCTIONS, k))
                for a, k in zip(asks, keys)]
        results, _ = _collect(j.batch(jobs), len(jobs))
        out: list[int | None] = []
        for (_, cands), k, probs in zip(asks, keys, results):
            if probs is None:
                out.append(next((i for i, c in enumerate(cands) if c.strong), None))
                continue
            best = max(k, key=lambda key: probs.get(key, 0.0))
            out.append(None if best == "none" else int(best[1:]))
        return out
    return pick


def fill(r: Run, j: JudgeAdapter | None = None) -> list[Candidate]:
    """Fill `{{...}}` tokens from the request. Code proposes and writes every value; the judge picks.

    Placement is a safety property: `rm -rf {{path}}` and `rm -rf ~` are different
    commands, and the gate must grade the one that will actually run.
    """
    if not r.line:
        r.line, r.line_does = _default_line(r)
    if not r.line:
        return r.cands
    filled = fill_resolve(r.line, r.query, Sources.live(history=True), judge_pick(j) if j is not None else None,
                          r.choices)
    r.line, r.fills = filled.line, filled.fills
    done = [f for f in filled.fills if f.value]
    r.note(filled=len(done), unresolved=len(filled.unresolved),
           values=",".join(f"{f.token}={f.value}" for f in done) or None)
    return r.cands


# ---------------------------------------------------------------- gate


GATE_INSTRUCTIONS = (
    "`command_line` is about to be pasted into a shell by someone learning Linux. Could running it as written "
    "destroy, overwrite or send away data, or change the system outside the current directory?")
GATE_YES = ("It deletes, truncates, overwrites, formats, reboots, kills processes, changes permissions or ownership, "
            "installs or removes software, or uploads data somewhere.")
GATE_NO = "It only reads, lists, searches, prints or copies into a new location."


def gate(r: Run, j: JudgeAdapter | None) -> list[Candidate]:
    """Terminal, never skippable. Assembles the line and evaluates the four tiers.

    The judge may only RAISE a tier (`jiffy.gate.raise_tier`); if it errors or is
    absent the pattern verdict stands.
    """
    if not r.line:
        r.line, r.line_does = _default_line(r)
    r.assembled = assemble(r.line, r.query, r.fills)
    p = None
    if j is not None and r.line:
        try:
            p = j.noul({"command_line": r.line}, GATE_INSTRUCTIONS, yes=GATE_YES, no=GATE_NO)
        except Exception as e:   # noqa: BLE001 - the gate never fails the run
            r.note(judge_error=type(e).__name__)
            p = None
    r.verdict = gate_pkg.evaluate(r.line, judge_p=p, denylist_path=getattr(r.cfg, "denylist_path", None))
    r.note(tier=r.verdict.tier.name, rule=r.verdict.rule_id,
           destructive=None if p is None else round(float(p), 3))
    return r.cands


# ---------------------------------------------------------------- the runner


#: (name, function, uses the judge). Order is the contract; fill then gate are last.
STAGES: tuple[tuple[str, Callable[[Run, Any], list[Candidate]], bool], ...] = (
    ("retrieve", retrieve, True),
    ("pick", pick, True),
    ("invoke", invoke, True),
    ("critic", critic, True),
    ("compose", compose, True),
    ("fill", fill, True),
    ("gate", gate, True),
)


def _detail(d: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in d.items() if v is not None and v != [] and v != "")


def _sandbox(r: Run) -> Run:
    """A copy a late thread can scribble on harmlessly."""
    return replace(r, detail=dict(r.detail), signals=dict(r.signals))


def _merge(sandbox: Run, r: Run) -> None:
    r.detail, r.signals = sandbox.detail, sandbox.signals
    r.line, r.line_does, r.choices = sandbox.line, sandbox.line_does, sandbox.choices
    r.fills, r.assembled, r.verdict = sandbox.fills, sandbox.assembled, sandbox.verdict


class Pipeline:
    """One index, one config, one judge. Reusable across queries."""

    def __init__(self, docs: dict[str, Doc], cfg: Config | None = None,
                 judge_factory: Callable[[str], JudgeAdapter] | None = None) -> None:
        self.docs = docs
        self.cfg = cfg or load_config(write_default=False)
        self.judge_name = (self.cfg.judge or "").strip() or DEFAULT_JUDGE
        self._factory = judge_factory or (lambda name: judge_pkg.get(name, config=self.cfg))
        self._judge: JudgeAdapter | None = None

    @property
    def judge(self) -> JudgeAdapter:
        if self._judge is None:
            self._judge = self._factory(self.judge_name)
        return self._judge

    def _spent(self) -> float:
        return self._judge.cost_usd if self._judge is not None else 0.0

    def run(self, query: str, cancelled: Callable[[], bool] | None = None) -> Answer:
        """`cancelled` is polled between stages: a superseded run stops paying."""
        cancelled = cancelled or NEVER
        r = Run(query=query, docs=self.docs, cfg=self.cfg)
        traces: list[NodeTrace] = []
        t0 = time.perf_counter()
        for name, fn, judged in STAGES:
            check(cancelled)                     # Cancelled propagates: no trace, no answer
            traces.append(self._stage(name, fn, judged, r, cancelled))
        trace = Trace(query=query, nodes=traces, total_ms=(time.perf_counter() - t0) * 1000.0,
                      total_usd=sum(t.usd for t in traces))
        top = r.cands[0] if r.cands else EMPTY_CANDIDATE
        assembled = r.assembled or assemble(r.line, query, r.fills)
        verdict = r.verdict or gate_pkg.evaluate(assembled.line)   # unreachable: gate is terminal
        return Answer(assembled=assembled, candidate=top, alternates=list(r.cands[1:]),
                      verdict=verdict, trace=trace)

    def _stage(self, name: str, fn, judged: bool, r: Run, cancelled) -> NodeTrace:
        """One stage, timed, metered and degraded."""
        in_n, r.detail = len(r.cands), {}
        before = self._spent()
        t0 = time.perf_counter()
        degraded, err = False, ""
        try:
            if judged:
                self._budget_check(name)
            out = self._call(fn, judged, r, cancelled)
        except Exception as e:   # noqa: BLE001 - FutureTimeout, BudgetExceeded, JudgeError, bugs
            degraded, err = True, f"{type(e).__name__}: {e}".strip()[:160]
            out = self._fallback(name, r)
        r.cands = out
        detail = {"error": err, **r.detail} if degraded else r.detail
        return NodeTrace(node_id=name, kind=name, judge=self.judge_name if judged else "",
                         in_n=in_n, out_n=len(out), ms=(time.perf_counter() - t0) * 1000.0,
                         usd=self._spent() - before, degraded=degraded, detail=_detail(detail))

    def _call(self, fn, judged: bool, r: Run, cancelled) -> list[Candidate]:
        """Per-stage timeout. A stage that overruns is a degradation, not a hang.

        The stage runs against a SANDBOX copy, merged back only if it finished in
        time: a timed-out thread cannot be killed and keeps writing, so it must
        write somewhere no later stage reads.
        """
        timeout = self.cfg.node_timeout_s
        if not judged or not timeout:
            return fn(r, None)
        check(cancelled)                      # last gate before a paid thread starts
        j = self.judge
        sandbox = _sandbox(r)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jiffy-stage")
        try:
            out = pool.submit(fn, sandbox, j).result(timeout=float(timeout))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        _merge(sandbox, r)
        return out

    def _budget_check(self, name: str) -> None:
        """jiffy's own cap, checked before a paid stage runs."""
        if getattr(self.judge, "paid", False) and self._spent() >= self.cfg.usd_cap:
            raise BudgetExceeded(f"jiffy cap ${self.cfg.usd_cap:.4f} reached (${self._spent():.4f} spent); "
                                 f"stage {name!r} degrades")

    def _fallback(self, name: str, r: Run) -> list[Candidate]:
        """Keep what the previous stage produced. The gate still produces a verdict."""
        cands = list(r.cands)
        if name == "gate":
            r.assembled = r.assembled or assemble(r.line, r.query, r.fills)
            r.verdict = r.verdict or gate_pkg.evaluate(r.assembled.line)
        if name == "fill":                 # the judge failed or ran out: the code-only fill stands
            fill(r, None)
        if name in ("invoke", "critic") and not r.line and cands:
            ex = _examples(cands[0].doc)
            if ex:
                r.line, r.line_does = ex[0].line, ex[0].does
        return cands

