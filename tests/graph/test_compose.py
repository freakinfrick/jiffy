import pytest

from jiffy.corpus import options as options_pkg
from jiffy.corpus.options import Option, Positional, Spec
from jiffy.fill import sources
from jiffy.graph import compose as comp
from jiffy.graph import pipeline as P
from jiffy.graph.pipeline import Pipeline, Run, STAGES
from jiffy.judge.backends import _Batching
from jiffy.judge.base import JudgeAdapter
from jiffy.types import Candidate, Doc, Example

LS = Spec("ls", ("ls [OPTION]... [FILE]...",), (Positional("FILE", True, True),), (
    Option(("-a", "--all"), desc="do not ignore entries starting with ."),
    Option(("-t",), desc="sort by time, newest first"),
    Option(("-h", "--human-readable"), desc="with -l and -s, print sizes like 1K 234M 2G etc."),
    Option(("-1",), desc="list one file per line"),
    Option(("--help",), desc="display this help and exit"),
))
FIND = Spec("find", ("find [-H] [starting-point...] [expression]",),
            (Positional("starting-point", True, True), Positional("expression", True)), (
    Option(("-size",), "n[cwbkMG]", desc="File uses less than, more than or exactly n units of space"),
    Option(("-name",), "pattern", desc="Base of file name matches shell pattern pattern"),
    Option(("-maxdepth",), "levels", desc="Descend at most levels levels of directories"),
))
GITLOG = Spec("git log", ("git log [<options>] [<revision range>] [[--] <path>...]",),
              (Positional("revision range", True), Positional("path", True, True)), (
    Option(("--oneline",), desc='shorthand for "--pretty=oneline --abbrev-commit"'),
    Option(("-n", "--max-count"), "<number>", joiner="=", desc="Limit the number of commits to output."),
))
GREP = Spec("grep", ("grep [OPTION...] PATTERNS [FILE...]",),
            (Positional("PATTERNS"), Positional("FILE", True, True)), (
    Option(("-r", "--recursive"), desc="Read all files under each directory, recursively"),
    Option(("--include",), "GLOB", joiner="=", desc="Search only files whose base name matches GLOB"),
))
CHMOD = Spec("chmod", ("chmod [OPTION]... MODE[,MODE]... FILE...",),
             (Positional("MODE", False, True), Positional("FILE", False, True)), ())


# ---------------------------------------------------------------- pure code


@pytest.mark.parametrize("spec,chosen,line", [
    (LS, [], "ls {{?path/to/file}}"),
    (LS, ["-a", "-t"], "ls -a -t {{?path/to/file}}"),
    (FIND, ["-size"], "find {{?path/to/directory}} -size {{size}}"),
    (GITLOG, ["--oneline", "-n"], "git log --oneline -n {{n}} {{?path/to/file}}"),
    (GREP, ["-r", "--include"], "grep -r --include={{glob}} {{pattern}} {{?path/to/file}}"),
    (CHMOD, [], "chmod {{MODE}} {{path/to/file}}"),
])
def test_build_line_leaves_every_positional_to_fill(spec, chosen, line):
    """Compose writes flags; values are fill's. An optional positional is a `{{?token}}`."""
    assert comp.build_line(spec.name, spec, [spec.option(f) for f in chosen], "any request") == line


def test_find_puts_options_after_the_starting_point():
    line = comp.build_line("find", FIND, [FIND.option("-name")], "find the python files under /srv")
    assert line == "find {{?path/to/directory}} -name {{pattern}}"


def test_optional_positionals_fill_or_drop():
    """Fill writes a stated one and drops an unstated one."""
    from jiffy.fill import Sources, resolve
    line = comp.build_line("find", FIND, [FIND.option("-name")], "")
    assert resolve(line, "find the python files under /srv", Sources()).line == "find /srv -name {{pattern}}"
    assert resolve(comp.build_line("ls", LS, [LS.option("-a")], ""), "show hidden entries",
                   Sources()).line == "ls -a"


def test_spelling_attaches_the_argument_the_documented_way():
    assert comp.spell(GITLOG.option("-n")) == "-n {{n}}"          # short: a space, never `-n={{n}}`
    assert comp.spell(Option(("--max-count",), "<number>", joiner="=")) == "--max-count={{n}}"
    assert comp.spell(Option(("--color",), "WHEN", optional=True, joiner="=")) == "--color"
    assert comp.spell(FIND.option("-maxdepth")) == "-maxdepth {{n}}"
    assert comp.spell(Option(("-type",), "c")) == "-type {{type}}"
    lines = Option(("-n", "--lines"), "N", optional=True, joiner="=")
    assert comp.spell(lines, query="show the entries") == "-n"                   # nothing stated: left off
    assert comp.spell(lines, query="show the 20 most recent entries") == "--lines={{n}}"     # never `{{c}}`: that reads as an extension


def test_relevant_short_list():
    names = [o.short for o in comp.relevant(LS, "list files sorted by time, newest first, human readable")]
    assert names[:2] == ["-t", "-h"] or set(names[:2]) == {"-t", "-h"}
    assert "--help" not in [o.short for o in comp.relevant(LS, "help me display this")]
    assert [o.short for o in comp.relevant(GITLOG, "one line per commit")][0] == "--oneline"




# ---------------------------------------------------------------- the stage


class Judge(_Batching, JudgeAdapter):
    """Says yes to options in `want`; grades a line by `grade(line)`."""

    paid = False

    def __init__(self, want=(), grade=lambda line: 0.9, fills=()):
        super().__init__()
        self.fills = set(fills)          # fill picks: the proposal with one of these values, else none
        self.want, self.grade, self.asked = set(want), grade, []

    def choice(self, state, instructions, options):
        if "blank" in state:
            hit = next((k for k in options if state["values"][k].split("  (")[0] in self.fills), "none")
            return {k: float(k == hit) for k in options}
        return {k: 1.0 / len(options) for k in options}

    def noul(self, state, instructions, yes=None, no=None):
        if "option" in state:
            flag = state["option"]["flag"].split()[0].split("=")[0]
            self.asked.append(flag)
            return 0.9 if flag in self.want else 0.1
        return self.grade(state["invocation"]["line"])


def _doc(name, line):
    return Doc(name, "common", f"{name} does things", [Example("does", line)], "tldr")


@pytest.fixture
def world(monkeypatch, tmp_path):
    specs = {"ls": LS, "find": FIND, "git log": GITLOG}
    monkeypatch.setattr(options_pkg, "spec", lambda name, corpus_dir=None, live=True: specs.get(name))
    monkeypatch.setattr(P.shutil, "which", lambda n: None if n == "fd" else f"/usr/bin/{n}")
    mime = tmp_path / "mime.types"
    mime.write_text("text/x-python py\ntext/markdown md markdown\n")
    monkeypatch.setattr(sources, "MIME_TYPES", mime)          # this box's table must not decide a test

    class Cfg:
        corpus_dir = tmp_path
        judge = "stub"
    return Cfg()


def _ctx(cfg, judge, query, names, critic_p):
    docs = {n: _doc(n, f"{n} {{{{x}}}}") for n in names}
    c = Run(query=query, docs=docs, cands=[Candidate(n, docs[n]) for n in names], cfg=cfg)
    c.line, c.line_does = f"{names[0]} {{{{x}}}}", "does"
    if critic_p is not None:
        c.signals["critic_p"] = critic_p
    return c


def test_does_not_fire_when_the_critic_passed(world):
    j = Judge()
    c = _ctx(world, j, "list python files in the home directory", ["ls"], critic_p=0.8)
    out = P.compose(c, j)
    assert c.detail["fired"] is False and c.line == "ls {{x}}" and not j.asked
    assert [x.name for x in out] == ["ls"]


def test_does_not_fire_without_a_critic(world):
    c = _ctx(world, Judge(), "anything", ["ls"], critic_p=None)
    P.compose(c, Judge())
    assert c.detail["fired"] is False


def test_the_pane_case_ships_ls_glob(world):
    """fd is on top but has no man page; ls composes `ls ~/*.py` and beats the incumbent."""
    j = Judge(grade=lambda line: 0.8 if line == "ls ~/*.py" else 0.1, fills={"~/*.py"})
    c = _ctx(world, j, "list python files in the home directory", ["fd", "find", "ls"], critic_p=0.13)
    out = P.compose(c, j)            # fd is skipped, not counted
    assert c.detail["line"] == "ls ~/*.py" and c.detail["shipped"] is True
    assert out[0].name == "ls" and c.signals["compose_p"] == pytest.approx(0.8)


def test_composes_the_judged_flags(world):
    j = Judge(want={"--oneline", "-n"})
    c = _ctx(world, j, "show the last 5 commits as one line each", ["git log"], critic_p=0.05)
    P.compose(c, j)
    assert c.line == "git log --oneline -n {{n}} {{?path/to/file}}"
    assert "-n" in c.line_does and "--oneline" in c.line_does


def test_keeps_the_incumbent_when_composing_is_no_better(world):
    j = Judge(grade=lambda line: 0.1)
    c = _ctx(world, j, "list python files in the home directory", ["ls"], critic_p=0.3)
    out = P.compose(c, j)
    assert c.line == "ls {{x}}" and c.detail["shipped"] is False and out[0].name == "ls"


def test_caps_the_number_of_flags(world, monkeypatch):
    j = Judge(want={"-a", "-t", "-h", "-1"})
    c = _ctx(world, j, "list all entries sorted by time newest first human readable one per line", ["ls"], 0.1)
    monkeypatch.setattr(P, "COMPOSE_MAX_OPTS", 2)
    P.compose(c, j)
    assert len(c.line.split()) == 4                         # ls + 2 flags + the optional positional


def test_failure_passes_through_untouched(world, monkeypatch):
    class Down(Judge):
        def choice(self, *a, **k):
            raise RuntimeError("down")
    c = _ctx(world, Down(), "list python files in the home directory", ["ls", "find"], critic_p=0.1)
    with pytest.raises(Exception):
        P.compose(c, Down())                               # the pipeline degrades it...
    kept = Pipeline({}, cfg=world)._fallback("compose", c)
    assert [x.name for x in kept] == ["ls", "find"] and c.line == "ls {{x}}"   # ...without re-sorting


def test_compose_runs_right_after_the_critic():
    kinds = [name for name, _, _ in STAGES]
    assert kinds.index("compose") == kinds.index("critic") + 1


def test_the_composed_line_is_graded_filled_and_shipped_as_a_template(world):
    """The critic grades what will run; fill writes it later, from the same (cached) picks."""
    j = Judge(grade=lambda line: 0.8 if line == "ls ~/*.md" else 0.1, fills={"~/*.md"})
    c = _ctx(world, j, "list markdown files in the home directory", ["ls"], critic_p=0.1)
    P.compose(c, j)
    assert c.line == "ls {{?path/to/file}}" and c.detail["line"] == "ls ~/*.md"
    P.fill(c, j)
    assert c.line == "ls ~/*.md"


def test_untyped_flag_arguments_are_closed_to_their_listed_values():
    d = Option(("-d", "--display"), "TYPE", desc="Display only: MEMORY, TEMPERATURE, POWER",
               values=("MEMORY", "TEMPERATURE", "POWER"))
    q = Option(("--query-gpu",), "query-gpu", joiner="=", desc="Information about GPU.")
    n = Option(("-n", "--max-count"), "<number>", joiner="=", desc="Limit the number of commits.")
    assert comp.closed([d, q, n]) == {"type": ("MEMORY", "TEMPERATURE", "POWER"), "query-gpu": ()}


def test_a_mode_flag_comes_with_the_flags_that_need_it():
    q = Option(("-q", "--query"), desc="Display GPU or Unit info.")
    d = Option(("-d", "--display"), "TYPE", desc="Display only selected information", needs="-q")
    smi = Spec("nvidia-smi", ("nvidia-smi [OPTION1 [ARG1]] ...",), (), (q, d))
    assert comp.with_leaders(smi, [d]) == [q, d]
    assert comp.with_leaders(smi, [q, d]) == [q, d]            # never twice
    assert comp.build_line("nvidia-smi", smi, comp.with_leaders(smi, [d]), "gpu temps") == "nvidia-smi -q -d {{type}}"


def test_prune_drops_an_option_that_repeats_another():
    big = Option(("-S",), desc="sort by file size, largest first")
    sort = Option(("--sort",), "WORD", joiner="=", desc="sort by WORD instead of name: none (-U), size (-S), time (-t)")
    assert comp.prune([sort, big]) == [big]                    # --sort names -S as one of its forms
    extent = Option(("-extent",), "geometry", desc="set the image size")
    scale = Option(("-scale",), "geometry", desc="scale the image")
    assert comp.prune([extent, scale]) == [extent]             # one {{geometry}} blank, best first
    assert comp.prune([big, extent]) == [big, extent]


def test_a_field_list_is_a_number():
    f = Option(("-f", "--fields"), "LIST", desc="select only these fields")
    assert comp.arg_token(f) == "n"
    assert comp.arg_token(Option(("--list",), "LIST", desc="use this list of names")) == "list"
