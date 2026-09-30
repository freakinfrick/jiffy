import json


from jiffy.corpus import helpscan, index
from jiffy.types import Example


def test_installed_is_the_first_word_on_path():
    assert index.installed("sh") and index.installed("sh -c true")
    assert not index.installed("zzz-no-such-command-here")


def test_merge_precedence(tmp_path):
    (tmp_path / "machine.json").write_text(json.dumps({"meta": {}, "docs": {
        "tar": {"plat": "common", "desc": "archiver", "source": "tldr", "ex": [{"d": "x", "c": "tar -xf a"}]}}}))
    (tmp_path / "help.json").write_text(json.dumps({"docs": {
        "tar": {"name": "tar", "plat": "linux", "desc": "HELP", "ex": [{"d": "x", "c": "tar -x"}]},
        "zzznew": {"name": "zzznew", "plat": "linux", "desc": "only in help", "ex": [{"d": "x", "c": "zzznew -x"}]}}}))
    d = index.load(("machine", "help"), corpus_dir=tmp_path)
    assert d["tar"].source == "tldr" and d["zzznew"].source == "help"


def test_parse_help():
    text = "Usage: foo [OPTION]... FILE\nDo the foo thing.\n\n  -v, --verbose   be chatty\n  -q              be quiet\n"
    d = helpscan.parse_help("foo", text)
    assert d.source == "help" and d.examples[0] == Example("synopsis of foo", "foo [OPTION]... FILE")
    assert any(e.line == "foo --verbose" and e.does == "be chatty" for e in d.examples)
    assert helpscan.parse_help("foo", "nothing here") is None


def test_helpscan_timeout(tmp_path):
    hang = tmp_path / "hang"
    hang.write_text("#!/bin/sh\nsleep 30\n")
    hang.chmod(0o755)
    assert helpscan._run(str(hang))[0] == "timeout"
