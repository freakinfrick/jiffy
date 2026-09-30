"""The argument surface, and the two things it must never do."""
from __future__ import annotations

import pathlib
import re


from jiffy import cli


def test_subcommands_and_global_flags():
    ap = cli.build_parser()
    listed = {a.dest for a in ap._actions}
    assert {"judge", "explain"} <= listed and not {"graph", "seed"} & listed
    sub = [a for a in ap._actions if a.dest == "cmd"][0]
    assert {"index", "bench", "learn", "config"} == set(sub.choices)


def test_bare_invocation_is_the_tui():
    assert cli.build_parser().parse_args([]).cmd is None


def test_bench_and_learn_are_passthroughs_not_re_declared_flags():
    """Their flags belong to their own units; cli.py must not copy them."""
    assert cli.PASSTHROUGH == ("bench", "learn")
    head, through, rest = cli._split(["--judge", "jev", "bench", "--limit", "2", "--json"])
    assert (head, through, rest) == (["--judge", "jev"], "bench", ["--limit", "2", "--json"])
    src = pathlib.Path(cli.__file__).read_text()
    assert "--json" not in src and "--usd-cap" not in src and "--dry" not in src


def test_passthrough_forwards_verbatim(monkeypatch):
    seen: list[list[str]] = []

    class FakeBench:
        @staticmethod
        def main(argv):
            seen.append(argv)
            return 0

    import sys
    monkeypatch.setitem(sys.modules, "jiffy.bench", FakeBench)
    assert cli.main(["bench", "--limit", "2", "--json"]) == 0
    assert seen[0] == ["--limit", "2", "--json"]


def test_help_scan_is_reachable_only_from_the_index_subcommand():
    """It EXECUTES PATH binaries. Never implicitly, never on a cold corpus."""
    src = pathlib.Path(cli.__file__).read_text()
    body = src[src.index("def _cmd_index"):src.index("def _passthrough")]
    assert src.count("helpscan.scan") == 1
    assert "if args.help_scan:" in body

    tui_dir = pathlib.Path(cli.__file__).parent / "tui"
    for path in tui_dir.glob("*.py"):
        assert "helpscan" not in path.read_text(), path


def test_env_overrides_are_the_only_precedence_path(monkeypatch):
    monkeypatch.delenv("JIFFY_JUDGE", raising=False)
    args = cli.build_parser().parse_args(["--judge", "stub"])
    cli._apply_env(args)
    import os
    assert os.environ["JIFFY_JUDGE"] == "stub"


def test_a_query_is_answered_once_and_printed(monkeypatch, capsys):
    assert cli._query(["--explain", "give", "a", "file", "a", "new", "name"]) == \
        (["--explain"], "give a file a new name")
    assert cli._query(["index", "--help-scan"]) == (["index", "--help-scan"], "")
    assert cli._query(["--judge", "stub", "list", "files"]) == (["--judge", "stub"], "list files")

    from jiffy.graph import api
    from jiffy.types import Doc, Example
    docs = {"mv": Doc("mv", "common", "Move or rename files", (Example("Rename a file", "mv a b"),), "tldr")}
    monkeypatch.setattr(api, "corpus", lambda cfg=None: docs)
    monkeypatch.setenv("JIFFY_JUDGE", "stub")
    assert cli.main(["--explain", "rename", "a", "file"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("mv a b  # rename a file") and "retrieve" in out and "gate" in out


def test_without_a_jev_key_jiffy_says_how_to_add_one_and_exits(monkeypatch, tmp_path, capsys):
    import jiffy.judge.backends as b
    monkeypatch.setattr(b, "KEYFILE", tmp_path / "no" / "api_key")
    monkeypatch.delenv("JIFFY_JUDGE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import importlib
    import jiffy.config as config_mod
    importlib.reload(config_mod)
    assert cli.main(["list", "files"]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and str(tmp_path / "no" / "api_key") in err[0]
    assert cli.main(["config"]) == 0            # no key needed to look at the config


def test_config_subcommand_prints_the_usd_cap(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import importlib
    import jiffy.config as config_mod
    importlib.reload(config_mod)
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"usd_cap\s+\$\d", out)
    assert "persist   history=False accepted=False aliases=False" in out


def test_a_judge_outage_is_named_not_reported_as_no_match(monkeypatch, capsys):
    from jiffy.graph import api
    from jiffy.judge.errors import JudgeError
    from jiffy.types import Doc
    import jiffy.judge.backends as b

    def broke(self, state, instructions, options):
        raise JudgeError("402 no available credits")
    monkeypatch.setattr(b.Stub, "choice", broke)
    monkeypatch.setattr(api, "corpus", lambda cfg=None: {"mv": Doc("mv", "common", "Move files", (), "tldr")})
    monkeypatch.setenv("JIFFY_JUDGE", "stub")
    assert cli.main(["rename", "a", "file"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("judge unavailable:") and "402" in err
