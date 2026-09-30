"""The two rules: code fills and the judge only picks; nothing is ever invented."""
from __future__ import annotations

from pathlib import Path

import pytest

from jiffy.fill import Sources, candidates_for, quote, resolve
from jiffy.fill.sources import MIME_TYPES
from jiffy.fill.tokens import parse

BTRFS = "sudo btrfs {{[f|filesystem]}} du {{[-s|--summarize]}} {{path/to/directory}}"


def by_value(want):
    """A scripted judge: pick the proposal whose value is `want[token]`, else none."""
    asked = []

    def pick(query, line, asks):
        asked.append([t.raw for t, _ in asks])
        out = []
        for t, cands in asks:
            vals = [c.value for c in cands]
            out.append(vals.index(want[t.raw]) if want.get(t.raw) in vals else None)
        return out
    pick.asked = asked
    return pick


LIVE_DIRS = Sources(cwd=Path("."), home=Path.home())


def test_the_case_that_started_this():
    """Two spellings and a place the user named; the judge picks `~` from the shell's places."""
    out = resolve(BTRFS, "how much storage does the home directory take", LIVE_DIRS,
                  by_value({"path/to/directory": "~"}))
    assert out.line == "sudo btrfs f du -s ~"
    assert out.unresolved == ()


def test_without_a_judge_a_place_is_never_guessed():
    """The anchors are weak: code alone never writes `~` for "home directory"."""
    out = resolve(BTRFS, "how much storage does the home directory take", LIVE_DIRS)
    assert out.line == "sudo btrfs f du -s {{path/to/directory}}"


def test_a_spelling_is_resolved_with_no_source_at_all():
    out = resolve("git {{[co|checkout]}} {{branch}}", "", Sources())
    assert out.line.startswith("git co ")
    assert [f.source for f in out.fills if f.token.startswith("[")] == ["spelling"]


def test_nothing_is_invented():
    """The whole safety story: no evidence means the token stays a token."""
    out = resolve("rm -rf {{path/to/directory}}", "delete the thing", Sources())
    assert out.line == "rm -rf {{path/to/directory}}"
    assert [f.token for f in out.unresolved] == ["path/to/directory"]


def test_the_judge_may_choose_none():
    """Places are on offer for every directory slot; `none` keeps the token."""
    out = resolve("rm -rf {{path/to/directory}}", "delete the thing", LIVE_DIRS, by_value({}))
    assert out.line == "rm -rf {{path/to/directory}}"


def test_an_optional_token_left_unfilled_is_dropped():
    out = resolve("ls -a {{?path/to/file}}", "show hidden entries", Sources())
    assert out.line == "ls -a" and out.fills == ()
    out = resolve("ls -a {{?path/to/file}}", "show hidden entries in /srv", Sources())
    assert out.line == "ls -a /srv"


def test_a_head_count_defaults_when_unstated():
    """`head -n {{n}}` with no number falls back to head's own 10, marked as a default."""
    out = resolve("du -sh {{path/to/directory}}/* | sort -rh | head -n {{n}}",
                  "show the biggest folders", Sources())
    assert out.line.endswith("head -n 10")
    n = next(f for f in out.fills if f.token == "n")
    assert n.value == "10" and n.source == "default"
    assert "{{n}}" not in out.line            # the path token may stay; the count must not


def test_a_stated_count_beats_the_default():
    out = resolve("du -sh {{path/to/directory}}/* | sort -rh | head -n {{n}}",
                  "show the 20 biggest folders", Sources())
    assert out.line.endswith("head -n 20")
    n = next(f for f in out.fills if f.token == "n")
    assert n.source == "query"


def test_a_non_head_count_is_not_defaulted():
    """`tree -L {{level}}` has no sensible default; it must stay a token."""
    out = resolve("tree -L {{level}} {{path/to/directory}}", "show a level tree of here", Sources())
    assert "{{level}}" in out.line


def test_a_partially_filled_line_keeps_the_tokens_it_could_not_answer():
    out = resolve("find {{path/to/directory}} -name '{{*.ext}}'", "find big files named *.log", Sources())
    assert "'*.log'" in out.line
    assert "{{path/to/directory}}" in out.line


def test_the_judge_is_asked_once_for_every_token_with_a_proposal():
    pick = by_value({"path/to/directory": "/two", "n": "5"})
    out = resolve("du -sh {{path/to/directory}} | head -n {{n}}", "size of /one and /two, top 5", Sources(), pick)
    assert pick.asked == [["path/to/directory", "n"]]          # one call, both tokens
    assert out.line == "du -sh /two | head -n 5"


def test_nothing_on_offer_asks_nothing():
    pick = by_value({})
    resolve("git {{[co|checkout]}} {{branch}}", "", Sources(), pick)
    assert pick.asked == []


def test_a_judge_failure_falls_back_to_the_first_strong_proposal():
    def boom(query, line, asks):
        raise RuntimeError("judge down")

    out = resolve("du -sh {{path/to/directory}}", "size of /one and /two", Sources(), boom)
    assert out.line.endswith("/one")               # first source order, not an error


def test_an_out_of_range_pick_is_clamped_not_trusted():
    out = resolve("du -sh {{path/to/directory}}", "size of /one and /two", Sources(),
                  lambda q, line, asks: [99])
    last = candidates_for(parse("path/to/directory"), "size of /one and /two", Sources())[-1]
    assert out.line == f"du -sh {last.value}"


def test_a_short_answer_from_the_judge_is_a_failure():
    out = resolve("du -sh {{path/to/directory}}", "size of /one and /two", Sources(),
                  lambda q, line, asks: [])
    assert out.line.endswith("/one")


@pytest.mark.parametrize("value,line,expected", [
    ("*.log", "find . -name {{*.ext}}", "'*.log'"),          # bare: quote it
    ("*.log", "find . -name '{{*.ext}}'", "*.log"),          # tldr quoted it already
    ("~", "du -sh {{path/to/directory}}", "~"),              # quoting would break expansion
    ("My Documents", "ls {{dir}}", "'My Documents'"),        # a space needs quoting
])
def test_a_value_is_quoted_only_when_the_shell_would_act_on_it(value, line, expected):
    start = line.index("{{")
    end = line.index("}}") + 2
    assert quote(value, line, start, end) == expected


def test_candidates_are_ordered_query_first():
    """The user's own words outrank anything inferred about their box."""
    cands = candidates_for(parse("path/to/directory"), "list ~/somewhere", Sources.live())
    assert cands[0].source == "query"


def test_fills_record_what_happened_for_every_token():
    out = resolve(BTRFS, "how much storage does the home directory take", LIVE_DIRS,
                  by_value({"path/to/directory": "~"}))
    assert [f.token for f in out.fills] == ["[f|filesystem]", "[-s|--summarize]", "path/to/directory"]
    assert [f.source for f in out.fills] == ["spelling", "spelling", "env"]
    assert all(f.why for f in out.fills)
    assert all(out.line[f.start:f.end] == f.value for f in out.fills)


MIME = Sources(mime=MIME_TYPES) if MIME_TYPES.exists() else None
needs_mime = pytest.mark.skipif(MIME is None, reason="no /etc/mime.types here")


@needs_mime
def test_the_pane_case_fills_the_extension():
    out = resolve("fd {{[-e|--extension]}} {{txt}}", "list python files in the home directory", MIME,
                  by_value({"txt": "py"}))
    assert out.line == "fd -e py" and not out.unresolved


@needs_mime
def test_find_by_extension_uses_the_real_extension():
    s = Sources(mime=MIME_TYPES, cwd=Path("."), home=Path.home())
    out = resolve("find {{path/to/directory}} -name '{{*.ext}}'", "list python files in the home directory",
                  s, by_value({"path/to/directory": "~", "*.ext": "*.py"}))
    assert out.line == "find ~ -name '*.py'"


@needs_mime
def test_a_glob_in_a_path_slot_is_left_for_the_shell():
    s = Sources(mime=MIME_TYPES)
    out = resolve("ls {{?path/to/file}}", "list python files", s, by_value({"path/to/file": "*.py"}))
    assert out.line == "ls *.py"


def test_size_bounds_keep_their_direction():
    """The judge reads "bigger than"; code drops the sizes signed against the token."""
    s = Sources(cwd=Path("."), home=Path.home())
    q = "find every file bigger than 100 megabytes under /var"
    assert [c.value for c in candidates_for(parse("-10M"), q, s)] == ["-100M", "100M"]
    out = resolve("find {{path/to/directory}} -size {{+500k}} -size {{-10M}}", q, s,
                  by_value({"path/to/directory": "/var", "+500k": "+100M"}))
    assert out.line == "find /var -size +100M -size {{-10M}}"      # the upper bound was never stated


def test_a_closed_blank_is_offered_only_its_listed_values():
    """`nvidia-smi -d gpu` came from offering request words to a blank the page lists values for."""
    line, query = "nvidia-smi -d {{type}}", "show gpu temperature"
    asked = []

    def pick(q, ln, asks):
        asked.extend(c.value for _, cands in asks for c in cands)
        return [0 for _ in asks]

    out = resolve(line, query, LIVE_DIRS, pick, {"type": ("MEMORY", "TEMPERATURE", "POWER")})
    assert set(asked) == {"MEMORY", "TEMPERATURE", "POWER"}
    assert out.line == "nvidia-smi -d TEMPERATURE"      # the value the request mentions is offered first


def test_a_closed_blank_with_no_list_stays_a_blank():
    out = resolve("nvidia-smi --query-gpu={{query-gpu}}", "show gpu temperature", LIVE_DIRS,
                  by_value({"query-gpu": "gpu"}), {"query-gpu": ()})
    assert out.line == "nvidia-smi --query-gpu={{query-gpu}}"
