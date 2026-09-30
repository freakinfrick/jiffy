"""The user denylist adds to DENY and can never subtract from it."""
from __future__ import annotations

import warnings

import pytest

from jiffy.gate import classify, evaluate
from jiffy.gate.denylist import (
    DenylistWarning,
    deny_rules,
    load_user_rules,
    parse_denylist,
)
from jiffy.gate.rules import DENY_RULES
from jiffy.types import Tier
from tier_table import TABLE


def _write(tmp_path, text):
    p = tmp_path / "denylist.txt"
    p.write_text(text)
    return p


def test_user_rules_are_appended_after_the_builtins(tmp_path):
    path = _write(tmp_path, "terraform\\s+destroy\n")
    merged = deny_rules(path)
    assert merged[: len(DENY_RULES)] == DENY_RULES
    assert len(merged) == len(DENY_RULES) + 1


def test_a_user_line_promotes_a_line_to_deny(tmp_path):
    path = _write(tmp_path, "# my own rules\nterraform\\s+destroy\n")
    assert classify("terraform destroy -auto-approve").tier is Tier.CLEAN
    verdict = classify("terraform destroy -auto-approve", denylist_path=path)
    assert verdict.tier is Tier.DENY
    assert verdict.rule_id.startswith("user:")
    assert "denylist" in verdict.reason


def test_a_literal_line_works_without_regex_syntax(tmp_path):
    path = _write(tmp_path, "drop database production\n")
    assert classify("psql -c 'drop database production'", denylist_path=path).tier is Tier.DENY


def test_no_user_file_means_the_builtins_stand(tmp_path):
    missing = tmp_path / "nope.txt"
    assert load_user_rules(missing) == []
    assert deny_rules(missing) == DENY_RULES


def test_a_user_file_can_never_lower_a_builtin_verdict(tmp_path):
    """Every attempt at an 'allow' syntax is just another DENY pattern or noise."""
    path = _write(
        tmp_path,
        "!rm -rf /\n"
        "-rm -rf /\n"
        "allow: rm -rf /\n"
        "^(?!rm -rf /).*$\n",
    )
    for line, expected in TABLE:
        assert classify(line, denylist_path=path).tier >= expected


def test_a_broken_regex_warns_and_the_rest_of_the_file_still_loads(tmp_path):
    path = _write(tmp_path, "good\\s+first\n[unclosed(\nterraform\\s+destroy\n")
    with pytest.warns(DenylistWarning, match="line 2"):
        rules = load_user_rules(path)
    assert len(rules) == 3, "a bad line must not truncate the file"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DenylistWarning)
        assert classify("terraform destroy", denylist_path=path).tier is Tier.DENY
        # the unparseable line still denies its own literal text
        assert classify("echo [unclosed(", denylist_path=path).tier is Tier.DENY


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = _write(tmp_path, "\n# a comment\n   \n\treal\\s+rule\n   # indented comment\n")
    rules = load_user_rules(path)
    assert len(rules) == 1
    assert rules[0].rule_id.endswith(":4")


def test_trailing_comment_on_a_rule_line_is_stripped(tmp_path):
    path = _write(tmp_path, "terraform\\s+destroy   # never again\n")
    assert classify("terraform destroy", denylist_path=path).tier is Tier.DENY


def test_an_empty_file_changes_nothing(tmp_path):
    path = _write(tmp_path, "")
    for line, expected in TABLE:
        assert classify(line, denylist_path=path).tier is expected


def test_parse_denylist_never_raises_on_garbage():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DenylistWarning)
        rules = parse_denylist("*\n(((\n\\\n+\n?\nfine\n")
    assert len(rules) == 6


def test_evaluate_honours_the_user_denylist(tmp_path):
    path = _write(tmp_path, "terraform\\s+destroy\n")
    assert evaluate("terraform destroy", judge_p=0.0, denylist_path=path).tier is Tier.DENY


BOX_RULES = r"""
# the rules that used to be built in, as this box's denylist.txt carries them
\b(?:pkill|killall|kill)\b[^;&|]*server\.py
(?<![0-9])8101(?![0-9])
CUDA_VISIBLE_DEVICES\s*=\s*["']?[0-9,]*(?<![0-9])1(?![0-9])
\bnvidia-smi\b[^;&|]*(?:\s-i\s*|--id=)1\b
"""


def test_machine_specific_rules_work_from_a_denylist(tmp_path):
    """Phase 5 moved them out of the built-ins; a denylist brings them back, warning-free."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        path = _write(tmp_path, BOX_RULES)
        for line in ("pkill -f server.py", "curl http://127.0.0.1:8101/x", "CUDA_VISIBLE_DEVICES=0,1 python t.py",
                     "nvidia-smi -i 1"):
            assert classify(line, denylist_path=path).tier is Tier.DENY, line
        assert classify("CUDA_VISIBLE_DEVICES=10 python t.py", denylist_path=path).tier is Tier.CLEAN


def test_no_builtin_names_one_machine():
    """Built-ins must hold on any box: no ports, process names or GPU indexes of this one."""
    from jiffy.gate.rules import CONFIRM_RULES, DENY_RULES, NOTE_RULES
    text = " ".join(r.pattern.pattern + r.reason for r in (*DENY_RULES, *CONFIRM_RULES, *NOTE_RULES))
    for needle in ("8101", "server\\.py", "4090", "laya"):
        assert needle not in text, needle
