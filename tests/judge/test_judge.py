import json
import socket
import threading
import time

import pytest

from jiffy.judge import BudgetExceeded, JudgeUnavailable, available, get
from jiffy.judge._http import AnswerCache, Ledger
from jiffy.judge.backends import Jev, _HttpJudge


class Local(_HttpJudge):
    """A free backend on the Jev protocol: the seam a local judge will use."""
    url = "http://127.0.0.1:9/system_one"
    model = "local-test"


def test_registry_lists_jev_and_stub_only():
    assert set(available()) == {"jev", "stub"}


def test_stub_deterministic_and_seeded():
    s1, s2, s3 = get("stub"), get("stub"), get("stub")
    s3.seed = 7
    st = {"q": "find big files"}
    assert s1.noul(st, "fits?") == s2.noul(st, "fits?")
    assert 0.0 <= s1.noul(st, "fits?") <= 1.0
    assert s1.noul(st, "fits?") != s3.noul(st, "fits?")
    c = s1.choice(st, "which", ["a", "b", "c"])
    assert c == s2.choice(st, "which", ["a", "b", "c"])
    assert set(c) == {"a", "b", "c"} and abs(sum(c.values()) - 1) < 1e-9


def test_batch_order_and_exceptions():
    s = get("stub")

    def slow(i):
        def j():
            time.sleep(0.05 * (5 - i))
            return i
        return j

    def bad():
        raise JudgeUnavailable("x")

    out = s.batch([slow(0), slow(1), bad, slow(3)])
    assert out[0] == 0 and out[1] == 1 and out[3] == 3
    assert isinstance(out[2], JudgeUnavailable)


def test_cache_hit_no_second_post(tmp_path, monkeypatch):
    import jiffy.judge.backends as b
    calls = []

    def fake_post(url, body, **kw):
        calls.append(body)
        return {"answers": {"fits": {"noul": 0.7}}, "usage": {"input_tokens": 10}}

    monkeypatch.setattr(b, "post", fake_post)
    from dataclasses import replace
    from jiffy.config import load as load_config

    cfg_on = load_config(write_default=False)
    cfg_on = replace(cfg_on, persistence=replace(cfg_on.persistence, cache=True))

    j = Local(config=cfg_on, cache_path=tmp_path / "c.jsonl", ledger=Ledger(tmp_path / "l.json"))
    assert j.noul({"a": 1}, "q") == 0.7
    assert j.noul({"a": 1}, "q") == 0.7
    assert len(calls) == 1
    # persisted, because [persistence] cache = true: a fresh adapter on the
    # same file also hits
    j2 = Local(config=cfg_on, cache_path=tmp_path / "c.jsonl", ledger=Ledger(tmp_path / "l.json"))
    assert j2.noul({"a": 1}, "q") == 0.7 and len(calls) == 1
    j2.noul({"a": 2}, "q")
    assert len(calls) == 2


def test_cache_writes_nothing_to_disk_by_default(tmp_path, monkeypatch):
    """SPEC: no writes outside ~/.config/jiffy and the corpus cache until a
    toggle is on. The in-memory cache still works; the file is never created."""
    from jiffy.judge._http import Ledger
    import jiffy.judge.backends as b

    calls = []

    def fake_post(url, body, **kw):
        calls.append(body)
        return {"answers": {"fits": {"noul": 0.7}}, "usage": {"input_tokens": 10}}

    monkeypatch.setattr(b, "post", fake_post)
    path = tmp_path / "c.jsonl"
    j = Local(cache_path=path, ledger=Ledger(tmp_path / "l.json"))
    assert j.cache.disk is False
    assert j.noul({"a": 1}, "q") == 0.7
    assert j.noul({"a": 1}, "q") == 0.7
    assert len(calls) == 1          # memory cache still spares the second call
    assert not path.exists()        # but nothing was written


def test_a_firewall_refusal_is_resent_once_with_lookalike_slashes(tmp_path, monkeypatch):
    """The API's edge answers 403 to `/etc/hosts` in a request; the judge is never asked."""
    import jiffy.judge.backends as b
    from jiffy.judge.errors import JudgeError
    sent = []

    def fake_post(url, body, **kw):
        sent.append(body["state"])
        if "/etc/" in json.dumps(body["state"]):
            raise JudgeError("403 b'<!DOCTYPE html>'")
        return {"answers": {"fits": {"noul": 0.7}}, "usage": {"input_tokens": 10}}

    monkeypatch.setattr(b, "post", fake_post)
    j = Local(cache_path=tmp_path / "c.jsonl", ledger=Ledger(tmp_path / "l.json"))
    assert j.noul({"user_request": "edit /etc/hosts", "n": 1}, "q") == 0.7
    assert len(sent) == 2 and sent[1] == {"user_request": "edit \u2215etc\u2215hosts", "n": 1}
    assert j.noul({"user_request": "edit /etc/hosts", "n": 1}, "q") == 0.7 and len(sent) == 2   # cached

    def always_403(url, body, **kw):
        raise JudgeError("403 forbidden")
    monkeypatch.setattr(b, "post", always_403)
    with pytest.raises(JudgeError):
        j.noul({"other": "x"}, "q")


def test_cache_key_includes_adapter_and_model(tmp_path):
    assert AnswerCache("local", "m1", tmp_path / "a").key({}, {}) != AnswerCache("jev", "m1", tmp_path / "a").key({}, {})
    assert AnswerCache("jev", "m1", tmp_path / "a").key({}, {}) != AnswerCache("jev", "m2", tmp_path / "a").key({}, {})


def test_jev_ledger_cap_and_cache_free(tmp_path, monkeypatch):
    import jiffy.judge.backends as b
    from jiffy.config import Config
    calls = []

    def fake_post(url, body, **kw):
        calls.append(body)
        assert body["model"] == "jev-1.13.0"
        return {"answers": {"fits": {"noul": 0.5}}, "usage": {"input_tokens": 1_000_000}}

    monkeypatch.setattr(b, "post", fake_post)
    monkeypatch.setattr(Jev, "_key", lambda self: "k")
    led = Ledger(tmp_path / "l.json")
    j = Jev(config=Config(usd_cap=0.05), cache_path=tmp_path / "c.jsonl", ledger=led)
    j.noul({"s": 1}, "q")               # costs $0.042
    assert abs(led.spent() - 0.042) < 1e-9 and abs(j.cost_usd - 0.042) < 1e-9
    j.noul({"s": 1}, "q")               # cached: free
    assert len(calls) == 1 and abs(led.spent() - 0.042) < 1e-9
    j.noul({"s": 2}, "q")               # 0.042 < 0.05, allowed -> 0.084
    with pytest.raises(BudgetExceeded):
        j.noul({"s": 3}, "q")
    assert len(calls) == 2


def test_connection_refused_is_unavailable(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    j = Local(cache_path=tmp_path / "c.jsonl", ledger=Ledger(tmp_path / "l.json"),
             url=f"http://127.0.0.1:{port}/system_one")
    with pytest.raises(JudgeUnavailable):
        j.noul({"a": 1}, "q")
    assert isinstance(j.batch([lambda: j.noul({"a": 1}, "q")])[0], JudgeUnavailable)


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def test_get_passes_config_to_adapters_that_take_one():
    from jiffy.config import Config
    cfg = Config(usd_cap=9.5)
    assert get("jev", config=cfg).cfg.usd_cap == 9.5          # an override must reach the adapter
    assert get("stub", config=cfg) is not None                # adapters without `config` still build


def test_ledger_cap_is_per_day(tmp_path):
    day = ["2026-09-21"]
    led = Ledger(tmp_path / "l.json", today=lambda: day[0])
    led.add("jev", 10, 0.30)
    with pytest.raises(BudgetExceeded, match="daily"):
        led.check(0.30)
    day[0] = "2026-09-22"                        # a new day: the cap resets, the lifetime total does not
    led.check(0.30)
    assert led.spent_today() == 0.0 and abs(led.spent() - 0.30) < 1e-9
    led.add("jev", 10, 0.05)
    assert led.read()["by_day"] == {"2026-09-21": 0.30, "2026-09-22": 0.05}


def test_ledger_without_by_day_starts_today_at_zero(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"usd": 5.0, "input_tokens": 0, "requests": 0, "by_adapter": {}}))
    led = Ledger(p)
    led.check(0.30)                              # old lifetime spend is not attributed to today
    assert led.spent() == 5.0


def _serve(handler_cls):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/system_one"


def _handler(conns, status=200, close_after=False):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            conns.append(self.client_address)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            out = json.dumps({"ok": True}).encode() if status < 400 else b"nope"
            self.send_response(status)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            if close_after:
                self.close_connection = True

        def log_message(self, *a):
            pass
    return H


def test_post_reuses_one_connection():
    from jiffy.judge._http import post
    conns = []
    srv, url = _serve(_handler(conns))
    try:
        for _ in range(5):
            assert post(url, {"a": 1}) == {"ok": True}
    finally:
        srv.shutdown()
    assert len(conns) == 1


def test_post_resends_once_when_server_dropped_the_idle_connection():
    # the server hangs up after each reply without saying so, so every pooled
    # socket is dead by the next call: each post must resend on a fresh one.
    from jiffy.judge._http import post
    conns = []
    srv, url = _serve(_handler(conns, close_after=True))
    try:
        for i in range(3):
            assert post(url, {"a": i}) == {"ok": True}
    finally:
        srv.shutdown()
    assert len(conns) == 3


def test_post_http_error_codes_map_as_before():
    from jiffy.judge import JudgeError
    from jiffy.judge._http import post
    srv, url = _serve(_handler([], status=400))
    try:
        with pytest.raises(JudgeError) as e:
            post(url, {"a": 1})
        assert not isinstance(e.value, JudgeUnavailable) and "400" in str(e.value)
    finally:
        srv.shutdown()
    srv, url = _serve(_handler([], status=503))
    try:
        with pytest.raises(JudgeUnavailable):
            post(url, {"a": 1})
    finally:
        srv.shutdown()
