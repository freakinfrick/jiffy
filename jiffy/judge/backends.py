"""Concrete adapters: `jev` (paid API) and `stub` (tests).

`_HttpJudge` carries the Jev protocol (`POST {model, state, questions}`) so a future
backend speaking it (a local judge) is a subclass that sets url/model/price. Lifted
from ~/jev-probes/jev.py (`ask`/`run` batching: ThreadPool, 16 workers, order preserved).
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..config import Config, load as load_config
from ._http import AnswerCache, Ledger, choice_q, noul_q, post, PRICE_PER_MTOK
from .base import JudgeAdapter, register
from .errors import JudgeError, JudgeUnavailable

WORKERS = 16
KEYFILE = Path.home() / ".config/typesafe/api_key"


#: U+2215 DIVISION SLASH. The API's edge firewall answers 403 to any request holding a
#: path like `/etc/hosts` (a man page summary, or the user's own words); the judge
#: reads this lookalike the same way. Used only to resend a request the firewall refused.
LOOKALIKE_SLASH = "\u2215"


def _defang(x: Any) -> Any:
    """`x` with every `/` in every string swapped for LOOKALIKE_SLASH."""
    if isinstance(x, str):
        return x.replace("/", LOOKALIKE_SLASH)
    if isinstance(x, dict):
        return {k: _defang(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_defang(v) for v in x]
    return x


def _clamp(x: Any) -> float:
    return min(1.0, max(0.0, float(x)))


def _norm(probs: dict, options: Sequence[str]) -> dict[str, float]:
    p = {o: _clamp(probs.get(o, 0.0)) for o in options}
    t = sum(p.values())
    return {o: v / t for o, v in p.items()} if t > 0 else {o: 1.0 / len(options) for o in options}


class _Batching:
    def batch(self, jobs: Iterable[Callable[[], Any]]) -> list:
        """Concurrent, order preserved; a failing job leaves its exception object in its slot."""
        def run(j):
            try:
                return j()
            except Exception as e:  # noqa: BLE001 - contract: exception object in the slot
                return e
        jobs = list(jobs)
        if not jobs:
            return []
        with ThreadPoolExecutor(min(WORKERS, len(jobs))) as pool:
            return list(pool.map(run, jobs))


class _HttpJudge(_Batching, JudgeAdapter):
    url = ""
    model = ""
    paid = False
    retries = 0

    def __init__(self, config: Config | None = None, cache_path: Path | None = None,
                 ledger: Ledger | None = None, url: str | None = None) -> None:
        super().__init__()
        self.cfg = config or load_config(write_default=False)
        self.url = url or self.url
        self.cache = AnswerCache(self.name, self.model, cache_path,
                                 disk=self.cfg.persistence.cache)
        self.ledger = ledger or Ledger()

    def _key(self) -> str:
        if not self.paid:
            return ""
        try:
            return KEYFILE.read_text().strip()
        except OSError as e:
            raise JudgeUnavailable(f"no api key at {KEYFILE}: {e}") from None

    def _ask(self, state: dict, question: dict, qid: str) -> dict:
        k = self.cache.key(state, question)
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        if self.paid:
            self.ledger.check(self.cfg.usd_cap)
        body = {"model": self.model, "state": state, "questions": {qid: question}}
        try:
            r = post(self.url, body, key=self._key(), timeout=self.cfg.http_timeout_s, retries=self.retries)
        except JudgeError as e:
            if not str(e).startswith("403 "):
                raise
            body["state"] = _defang(state)          # the firewall, not the judge, said no
            r = post(self.url, body, key=self._key(), timeout=self.cfg.http_timeout_s, retries=self.retries)
        try:
            ans = r["answers"][qid]
        except (KeyError, TypeError):
            raise JudgeError(f"malformed response from {self.url}: {str(r)[:200]}") from None
        toks = int(r.get("usage", {}).get("input_tokens", 0))
        usd = toks * PRICE_PER_MTOK / 1e6 if self.paid else 0.0
        self.cache.put(k, ans)
        self.cost_usd += usd
        if self.paid:
            self.ledger.add(self.name, toks, usd)
        return ans

    def choice(self, state, instructions, options):
        options = list(options)
        a = self._ask(state, choice_q(instructions, options), "pick")
        return _norm(a["probabilities"], options)

    def noul(self, state, instructions, yes=None, no=None):
        return _clamp(self._ask(state, noul_q(instructions, yes, no), "fits")["noul"])


@register("jev")
class Jev(_HttpJudge):
    """Paid TypeSafe API. Model pinned; metered against jiffy's own usd_cap."""
    url = "https://api.typesafe.ai/v1/systemone"
    model = "jev-1.13.0"
    paid = True
    retries = 3


@register("stub")
class Stub(_Batching, JudgeAdapter):
    """Deterministic, offline judge for tests. Importable: `from jiffy.judge import get; get("stub")`.

    Probabilities come from sha256(seed, sha256(state, instructions)[, option]) — same inputs, same
    outputs, across processes and runs (no Python `hash()`). `noul` returns a float in [0,1];
    `choice` returns a distribution over `options` summing to 1. No network, no cache, cost 0.
    Optional `fail_on` substring in `instructions` raises JudgeUnavailable, to exercise degradation.
    """

    def __init__(self, seed: int = 0, fail_on: str | None = None) -> None:
        super().__init__()
        self.seed, self.fail_on = seed, fail_on
        self.calls = 0

    def _u(self, *parts: Any) -> float:
        blob = json.dumps([self.seed, *parts], sort_keys=True, default=str, ensure_ascii=False)
        return int(hashlib.sha256(blob.encode()).hexdigest()[:12], 16) / 16 ** 12

    def _check(self, instructions: str) -> None:
        self.calls += 1
        if self.fail_on and self.fail_on in instructions:
            raise JudgeUnavailable("stub: induced failure")

    def choice(self, state, instructions, options):
        self._check(instructions)
        options = list(options)
        base = hashlib.sha256(json.dumps([state, instructions], sort_keys=True, default=str,
                                         ensure_ascii=False).encode()).hexdigest()
        w = {o: 0.05 + self._u(base, o) for o in options}
        t = sum(w.values())
        return {o: v / t for o, v in w.items()}

    def noul(self, state, instructions, yes=None, no=None):
        self._check(instructions)
        return self._u(state, instructions, yes, no)
