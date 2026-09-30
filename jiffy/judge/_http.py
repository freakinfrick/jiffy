"""Shared plumbing for the HTTP adapters: POST, answer cache, USD ledger, question builders.

Lifted from ~/jev-probes/jev.py (read-only source): `_post`, the per-question answer cache
keyed on the request, the token ledger, and the `noul`/`choice` question builders. Adapted:
jiffy's own cache dir + ledger + cap (config `usd_cap`), typed exceptions, per-call timeout,
one cache file per adapter, model pinned per adapter.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import http.client
import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .errors import BudgetExceeded, JudgeError, JudgeUnavailable

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "jiffy"
CACHE_DIR = DATA_DIR / "cache"
LEDGER_PATH = DATA_DIR / "ledger.json"     # jiffy's own; never jev-probes' out/spend.json
PRICE_PER_MTOK = 0.042                     # jev-1.13.0 input price, USD

_ledger_lock = threading.Lock()            # ledger file is shared across adapters in-process


def noul_q(instructions: str, yes: str | None = None, no: str | None = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if yes:
        q["criteria"] = {"true": yes, "false": no}
    return q


def choice_q(instructions: str, options) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": {o: None for o in options}}


class _Pool:
    """Idle keep-alive connections per (scheme, host, port), shared across threads.

    A fresh TLS connection to the Jev API costs ~145 ms (TCP ~70 + TLS ~75, measured
    2026-09-21) before the request is even sent; a reused one answers in ~70 ms. The
    graph makes ~6 calls in series per query, so reuse is most of jiffy's latency.
    `batch` runs on a throwaway ThreadPoolExecutor, so thread-locals would die with
    it; a locked free list survives.
    """

    def __init__(self) -> None:
        self._idle: dict[tuple, list[http.client.HTTPConnection]] = {}
        self._lock = threading.Lock()

    def get(self, u: urllib.parse.SplitResult, timeout: float) -> tuple[http.client.HTTPConnection, bool]:
        """(connection, reused)."""
        k = (u.scheme, u.hostname, u.port)
        with self._lock:
            idle = self._idle.get(k)
            if idle:
                c = idle.pop()
                c.timeout = timeout
                if c.sock is not None:
                    c.sock.settimeout(timeout)
                return c, True
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        return cls(u.hostname, u.port, timeout=timeout), False

    def put(self, u: urllib.parse.SplitResult, c: http.client.HTTPConnection) -> None:
        with self._lock:
            self._idle.setdefault((u.scheme, u.hostname, u.port), []).append(c)


_pool = _Pool()
_STALE = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
          http.client.CannotSendRequest)


def _send(u: urllib.parse.SplitResult, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """One POST over a pooled connection. A reused connection the server already
    closed fails before any response, so it is dropped and the request resent once
    on a fresh one; nothing was answered, so nothing is billed twice."""
    path = u.path + (f"?{u.query}" if u.query else "")
    while True:
        c, reused = _pool.get(u, timeout)
        try:
            c.request("POST", path, body=data, headers=headers)
            r = c.getresponse()
            body = r.read()
        except _STALE:
            c.close()
            if reused:
                continue
            raise
        except BaseException:
            c.close()
            raise
        if r.will_close:
            c.close()
        else:
            _pool.put(u, c)
        return r.status, body


def post(url: str, body: dict, key: str = "", timeout: float = 20.0, retries: int = 0) -> dict:
    """POST JSON. Connection failure/timeout -> JudgeUnavailable; other HTTP errors -> JudgeError."""
    data = json.dumps(body, ensure_ascii=False).encode()
    u = urllib.parse.urlsplit(url)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "curl/8"}
    last = "no attempt"
    for attempt in range(retries + 1):
        try:
            status, raw = _send(u, data, headers, timeout)
            if status < 400:
                return json.loads(raw)
            if status not in (429, 500, 502, 503, 529):
                raise JudgeError(f"{status} {raw[:300]!r}")
            last = f"HTTP {status}"
        except (http.client.HTTPException, TimeoutError, ConnectionError, OSError) as e:
            last = str(e) or type(e).__name__
            if retries == 0 or isinstance(e, ConnectionRefusedError) or "refused" in last.lower():
                break
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise JudgeUnavailable(f"{url}: {last}")


class Ledger:
    """USD ledger at LEDGER_PATH: {"usd": float, "input_tokens": int, "requests": int,
    "by_adapter": {}, "by_day": {"YYYY-MM-DD": usd}}.

    `usd` is the lifetime total, kept for the record. The cap is checked against
    TODAY's spend: a lifetime cap on a tool that costs fractions of a cent per
    query runs out one day and silently degrades every paid node from then on.
    Spend from before `by_day` existed is not attributed to any day.
    """

    def __init__(self, path: Path | None = None, today: Callable[[], str] | None = None):
        self.path = Path(path) if path else LEDGER_PATH
        self._today = today or (lambda: _dt.date.today().isoformat())

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"usd": 0.0, "input_tokens": 0, "requests": 0, "by_adapter": {}}

    def spent(self) -> float:
        return float(self.read().get("usd", 0.0))

    def spent_today(self) -> float:
        return float(self.read().get("by_day", {}).get(self._today(), 0.0))

    def check(self, cap: float) -> None:
        if self.spent_today() >= cap:
            raise BudgetExceeded(f"jiffy daily usd_cap ${cap:.4f} reached "
                                 f"(spent ${self.spent_today():.4f} today)")

    def add(self, adapter: str, tokens: int, usd: float) -> None:
        with _ledger_lock:
            s = self.read()
            s["usd"] = float(s.get("usd", 0.0)) + usd
            s["input_tokens"] = int(s.get("input_tokens", 0)) + tokens
            s["requests"] = int(s.get("requests", 0)) + 1
            a = s.setdefault("by_adapter", {}).setdefault(adapter, {"usd": 0.0, "input_tokens": 0})
            a["usd"] += usd
            a["input_tokens"] += tokens
            day = s.setdefault("by_day", {})
            day[self._today()] = float(day.get(self._today(), 0.0)) + usd
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(s, indent=1))


class AnswerCache:
    """One jsonl file per adapter; key = sha(adapter, model, state, question)."""

    def __init__(self, adapter: str, model: str, path: Path | None = None,
                 disk: bool = False):
        # `disk` is the [persistence] cache toggle. Off by default: SPEC allows
        # no writes outside ~/.config/jiffy and the corpus cache until a toggle
        # is on. In-memory caching is always active and writes nothing.
        self.adapter, self.model, self.disk = adapter, model, disk
        self.path = Path(path) if path else CACHE_DIR / f"{adapter}.jsonl"
        self._lock = threading.Lock()
        self._mem: dict[str, Any] | None = None

    def key(self, state: dict, question: dict) -> str:
        blob = json.dumps([self.adapter, self.model, state, question], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def _load(self) -> dict[str, Any]:
        if self._mem is None:
            self._mem = {}
            if self.disk and self.path.exists():
                for line in self.path.read_text().splitlines():
                    try:
                        r = json.loads(line)
                        self._mem[r["h"]] = r["a"]
                    except (ValueError, KeyError):
                        continue
        return self._mem

    def get(self, k: str):
        with self._lock:
            return self._load().get(k)

    def put(self, k: str, answer: Any) -> None:
        with self._lock:
            self._load()[k] = answer
            if not self.disk:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps({"h": k, "a": answer}, ensure_ascii=False) + "\n")
