"""The shipped form, and what happens when the request fights the shell.

Assembly itself lives in `jiffy.graph.assemble` (SPEC module map);
`jiffy.tui.assemble` is a re-export, and this file asserts that it stays one.
"""
from __future__ import annotations

import pathlib
import shlex
import subprocess


from jiffy.graph.assemble import assemble as graph_assemble
from jiffy.tui import shell
from jiffy.tui import assemble as tui_assemble
from jiffy.tui.assemble import assemble, sanitise
from jiffy.types import GateVerdict, Tier

CLEAN = GateVerdict(Tier.CLEAN, "", "")


def _bash_parses(line: str) -> bool:
    return subprocess.run(["bash", "-n"], input=line, text=True,
                          capture_output=True).returncode == 0


def test_the_line_is_built_by_the_graph_not_by_a_second_assembler():
    """The shim sanitises the request and delegates; it formats nothing itself."""
    src = pathlib.Path(tui_assemble.__file__).read_text()
    assert "_assemble(line, sanitise(request), fills)" in src
    assert "comment_line=" not in src and "Assembled(" not in src   # builds nothing itself
    for line, request in [("ls -la", "list files"), ("du -sh *", "space here")]:
        assert assemble(line, request) == graph_assemble(line, request)


def test_shipped_form():
    a = assemble("find . -name '*.log' -size +100M", "find big log files")
    assert a.comment_line == "find . -name '*.log' -size +100M  # find big log files"
    assert a.line == "find . -name '*.log' -size +100M"
    assert a.request == "find big log files"


def test_hash_in_the_request_leaves_exactly_one_comment():
    """The line stays a single valid shell command with one `#`."""
    a = assemble("du -sh *", "what is using space in #downloads")
    assert a.comment_line == "du -sh * a# what is using space in downloads".replace(" a#", "  #")
    assert a.comment_line.count("#") == 1
    assert a.comment_line.count("\n") == 0
    assert _bash_parses(a.comment_line)
    assert a.comment_line.split("  # ", 1)[0] == "du -sh *"
    assert shlex.split(a.comment_line, comments=True) == shlex.split("du -sh *")


def test_newline_in_the_request_cannot_open_a_second_shell_line():
    a = assemble("ls -la", "list files\nrm -rf /tmp/x")
    assert "\n" not in a.comment_line
    assert _bash_parses(a.comment_line)
    assert shlex.split(a.comment_line, comments=True) == ["ls", "-la"]


def test_sanitise_drops_only_hashes_and_control_characters():
    assert sanitise("  what is  using space in #downloads ") == "what is using space in downloads"
    assert sanitise("a\tb\r\nc") == "a b c"
    assert sanitise("it's a \"quote\"; ls `date` $(pwd)") == "it's a \"quote\"; ls `date` $(pwd)"


def test_empty_request_leaves_a_bare_line():
    assert assemble("ls -la", "").comment_line == "ls -la"


def test_the_graph_assembler_sanitises_at_the_source():
    """Fixed by the conductor after the verify unit found the bypass.

    Sanitising lives in jiffy.graph.assemble.sanitise_request, the single place
    that builds the comment, so every caller gets a single shell command —
    not only the callers that remember to clean their input first.
    """
    a = graph_assemble("ls -la", "list files\nrm -rf /tmp/x")
    assert "\n" not in a.comment_line
    assert a.comment_line == "ls -la  # list files rm -rf /tmp/x"
    assert graph_assemble("ls", "x # y").comment_line.count("#") == 1


def test_tui_refuses_to_execute_a_multi_line_assembly():
    """Defence in depth: even if something upstream produced two lines, the
    execution chokepoint refuses it. Kept after the source fix, because this
    guard is what held when the assembler did not."""
    calls: list[str] = []
    res = shell.execute("ls -la  # list files\nrm -rf /tmp/x", CLEAN,
                        runner=lambda line: calls.append(line) or 0)
    assert isinstance(res, shell.Refusal)
    assert calls == []
