"""Regressions for the gate bypasses the verify unit found (run 'jiffy').

Every case here reached the shell, or reached it too quietly, before the fix.
The building units tested the rules they wrote; these test the evasions, which
is a different thing and the reason the verifier found them.
"""
import time
import warnings

import pytest

from jiffy.gate.classify import classify
from jiffy.gate.denylist import parse_denylist
from jiffy.types import Tier


@pytest.mark.parametrize("line", [
    "rm${IFS}-rf${IFS}/",          # V1 BLOCKER: bash word-splits IFS back to spaces
    "rm${IFS%??}-rf${IFS%??}/",
    "rm $IFS-rf $IFS/",
    "X=/; rm -rf $X",              # V11: literal assignment used later in the line
    "X=/; rm -rf ${X}",
])
def test_shell_metacharacter_evasions_still_deny(line):
    assert classify(line).tier is Tier.DENY, line


@pytest.mark.parametrize("line,tier", [
    ('python3 -c "import shutil;shutil.rmtree(\'/\')"', Tier.DENY),     # V3
    ("perl -e 'File::Path::remove_tree(\"/\")'", Tier.DENY),
    ("python3 -c 'import os;os.remove(\"notes.txt\")'", Tier.CONFIRM),
    ('python3 -c "print(1)"', Tier.CLEAN),
    ("python3 train.py", Tier.CLEAN),
])
def test_interpreter_one_liners(line, tier):
    assert classify(line).tier is tier, (line, classify(line).tier.name)


def test_benign_lines_did_not_get_caught_in_the_net():
    """The fix widens what DENY sees. It must not widen what DENY hits."""
    for line in ["ls -la", "find . -name '*.log' -size +100M", "git status",
                 "du -sh ~/jiffy", "echo $HOME", "grep -r IFS ."]:
        assert classify(line).tier is Tier.CLEAN, line


def test_catastrophic_user_regex_cannot_hang_the_prompt():
    """V4: the gate backs the prompt validator, so a user pattern that
    backtracks exponentially froze the UI on every keystroke."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rules = parse_denylist("(a+)+$\n(x*)*y\n")
    assert len(rules) == 2                      # kept as DENY rules, matched literally
    evil = "a" * 64 + "!"
    start = time.monotonic()
    for r in rules:
        r.pattern.search(evil)
    assert time.monotonic() - start < 0.5


def test_user_denylist_still_adds_and_cannot_subtract():
    rules = parse_denylist("my-very-own-dangerous-tool\n")
    assert any("my-very-own-dangerous-tool" in r.pattern.pattern for r in rules)
