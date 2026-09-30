"""The chokepoint, against a real $SHELL: tiers, exit codes, templates."""
from __future__ import annotations

import os

from jiffy import gate
from jiffy.tui import shell
from jiffy.types import GateVerdict, Tier

CLEAN = GateVerdict(Tier.CLEAN, "", "")
NOTE = GateVerdict(Tier.NOTE, "writes outside the working tree", "builtin:write")
CONFIRM = GateVerdict(Tier.CONFIRM, "recursive permission change", "builtin:chmod-r")


def test_a_clean_line_runs_and_keeps_its_exit_code():
    assert shell.execute("true", CLEAN).returncode == 0
    assert shell.execute("exit 3", CLEAN).returncode == 3


def test_note_runs_normally():
    assert isinstance(shell.execute("true", NOTE), shell.Result)


def test_confirm_needs_confirmation_and_then_runs():
    assert isinstance(shell.execute("true", CONFIRM), shell.Refusal)
    assert shell.execute("true", CONFIRM, confirmed=True).returncode == 0


def test_a_template_is_refused_even_when_clean():
    assert isinstance(shell.execute("find {{path}}", CLEAN), shell.Refusal)


def test_it_uses_the_user_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert shell.user_shell() == "/bin/bash"
    monkeypatch.delenv("SHELL")
    assert shell.user_shell() == "/bin/sh"


def test_the_gate_is_consulted_not_re_implemented():
    """`execute` asks `jiffy.gate.may_run`; it owns no tier logic of its own."""
    src = (os.path.dirname(shell.__file__) + "/shell.py")
    text = open(src).read()
    assert "gate.may_run" in text
    assert gate.may_run(CONFIRM, confirmed=False) is False



def test_ctrl_c_during_a_run_does_not_raise_in_jiffy():
    """The pane crash: SIGINT while waiting on the child must not raise KeyboardInterrupt."""
    import os
    import signal
    import threading
    from jiffy.tui.shell import _spawn

    before = signal.getsignal(signal.SIGINT)
    t = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))   # jiffy only, not the group
    t.start()
    try:
        assert _spawn("sleep 0.6") == 0
    finally:
        t.cancel()
    assert signal.getsignal(signal.SIGINT) is before     # jiffy's own handler is back


def test_the_command_still_dies_on_ctrl_c():
    """Held with a handler, not SIG_IGN, so the child keeps default SIGINT; 128+2 like bash."""
    from jiffy.tui.shell import _spawn
    assert _spawn("kill -INT $$") == 130
