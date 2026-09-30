import json
import shutil

import pytest

from jiffy.corpus import options
from jiffy.corpus.options import Option, Positional, parse_head, parse_man, parse_synopsis

# Excerpts in the exact layout `man` prints at MANWIDTH=200: heads at 7, text at 14.
LS = """\
LS(1)                                User Commands                               LS(1)

NAME
       ls - list directory contents

SYNOPSIS
       ls [OPTION]... [FILE]...

DESCRIPTION
       List  information  about the FILEs (the current directory by default).

       -a, --all
              do not ignore entries starting with .

       --block-size=SIZE
              with -l, scale sizes by SIZE when printing them; e.g., '--block-size=M'; see SIZE format below

       -c     with -lt: sort by, and show, ctime (time of last modification of file status information);
              with -l: show ctime and sort by name

       --color[=WHEN]
              colorize the output; WHEN can be 'always' (default if omitted), 'auto', or 'never'

       -1     list one file per line

       -A
       -o
       --print-file-name
              Precede each symbol by the name of the input file.

EXAMPLES
       -a, --bogus
              not an option definition: it is in EXAMPLES
"""

FIND = """\
SYNOPSIS
       find [-H] [-L] [-P] [-D debugopts] [-Olevel] [starting-point...] [expression]

TESTS
       -empty File is empty and is either a regular file or a directory.

       -mtime n
              File's data was last modified less than, more than or exactly n*24 hours ago.

       -name pattern
              Base of file name matches shell pattern pattern.

       -size n[cwbkMG]
              File uses less than, more than or exactly n units of space, rounding up.

STANDARDS CONFORMANCE
       -name  This option is supported, but POSIX conformance depends on the system.
"""

GITLOG = """\
SYNOPSIS
       git log [<options>] [<revision range>] [[--] <path>...]

OPTIONS
       -<number>, -n <number>, --max-count=<number>
           Limit the number of commits to output.

       --[no-]mailmap, --[no-]use-mailmap
           Use mailmap file to map author and committer names and email addresses.

       -L<start>,<end>:<file>, -L:<funcname>:<file>
           Trace the evolution of the line range.
"""


def test_ls_page():
    s = parse_man("ls", LS)
    assert s.synopsis == ("ls [OPTION]... [FILE]...",)
    assert s.positionals == (Positional("FILE", optional=True, repeat=True),)
    a = s.option("--all")
    assert a.flags == ("-a", "--all") and a.arg == "" and a.desc == "do not ignore entries starting with ."
    bs = s.option("--block-size")
    assert (bs.arg, bs.joiner, bs.optional) == ("SIZE", "=", False)
    assert s.option("--color").optional is True
    c = s.option("-c")                                     # description shares the head's line
    assert c.desc.startswith("with -lt: sort by") and c.desc.endswith("sort by name")
    assert s.option("-1").desc == "list one file per line"
    stack = s.option("-o")                                 # stacked heads share one description
    assert stack.flags == ("-A", "-o", "--print-file-name") and stack.desc.startswith("Precede")
    assert s.option("--bogus") is None                     # EXAMPLES is not a definitions section


def test_find_page():
    s = parse_man("find", FIND)
    assert [p.name for p in s.positionals] == ["starting-point", "expression"]
    assert s.option("-empty").arg == "" and s.option("-empty").desc.startswith("File is empty")
    assert s.option("-mtime").arg == "n"                   # `-mtime n` is not head + prose
    assert s.option("-size").arg == "n[cwbkMG]"
    assert s.option("-name").desc.startswith("Base of file name")   # first definition wins


def test_git_log_page():
    s = parse_man("git log", GITLOG)                       # heads at 7, text at 11: derived, not assumed
    assert [p.name for p in s.positionals] == ["revision range", "path"]
    n = s.option("-n")
    assert n.flags == ("-n", "--max-count") and n.arg == "<number>"
    assert {"--mailmap", "--no-mailmap", "--use-mailmap", "--no-use-mailmap"} <= set(s.option("--mailmap").flags)
    assert s.option("-L").joiner == ""


@pytest.mark.parametrize("head,flags,arg,joiner", [
    ("-a, --all", ("-a", "--all"), "", " "),
    ("-d, --max-depth=N", ("-d", "--max-depth"), "N", "="),
    ("-maxdepth levels", ("-maxdepth",), "levels", " "),
    ("-O[level]", ("-O",), "level", ""),
    ("-n, --lines=", ("-n", "--lines"), "lines", "="),
    ("--decorate[=short|full|auto|no]", ("--decorate",), "short|full|auto|no", "="),
])
def test_heads(head, flags, arg, joiner):
    o = parse_head(head)
    assert (o.flags, o.arg, o.joiner) == (flags, arg, joiner)


@pytest.mark.parametrize("head", ["-", "--", "- a lone dash", "not a flag", "-x this is far too much prose"])
def test_not_heads(head):
    assert parse_head(head) is None


def test_wrapped_synopsis_keeps_its_positionals():
    page = """SYNOPSIS
     ssh [-46AaCf] [-B bind_interface] [-b bind_address]
         [-J destination] [-p port]
         destination [command [argument ...]]

DESCRIPTION
     -p port
             Port to connect to on the remote host.
"""
    s = parse_man("ssh", page)
    assert s.positionals[0] == Positional("destination") and s.option("-p").arg == "port"


def test_synopsis_wrapped_at_the_same_indent():
    page = """SYNOPSIS
       tree  [-acdf]  [-L level [-R]] [-H baseHREF]
       [--inodes] [--]
       [directory ...]

DESCRIPTION
       -L level
              Max display depth of the directory tree.
"""
    s = parse_man("tree", page)
    assert len(s.synopsis) == 1 and s.positionals == (Positional("directory", True, True),)
    top = parse_man("top", """SYNOPSIS
       top -hv|-bcEeHiOSs1 -d secs -n max

       The traditional switches `-' and whitespace are optional.
""")
    assert top.synopsis == ("top -hv|-bcEeHiOSs1 -d secs -n max",) and top.positionals == ()


def test_a_flags_argument_is_not_a_positional():
    assert parse_synopsis("top -hv|-bcEeHiOSs1 -d secs -n max -u|U user -p pids -o field -w [cols]", "top") == ()
    assert parse_synopsis("chmod [OPTION]... --reference=RFILE FILE...", "chmod") == (Positional("FILE", False, True),)


def test_synopsis_shapes():
    chmod = parse_synopsis("chmod [OPTION]... MODE[,MODE]... FILE...", "chmod")
    assert chmod == (Positional("MODE", False, True), Positional("FILE", False, True))
    grep = parse_synopsis("grep [OPTION...] PATTERNS [FILE...]", "grep")
    assert grep == (Positional("PATTERNS"), Positional("FILE", True, True))


def test_short_spelling():
    assert Option(("--all", "-a")).short == "-a"


def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "read_man", lambda n, timeout=5.0: LS if n == "ls" else "")
    r = options.scan(["ls", "nope"], tmp_path, workers=2, log=lambda s: None)
    assert list(r["specs"]) == ["ls"] and r["missing"] == ["nope"]
    json.loads(options.path(tmp_path).read_text())
    s = options.spec("ls", tmp_path, live=False)
    assert s == parse_man("ls", LS)
    assert options.spec("nope", tmp_path, live=False) is None


@pytest.mark.skipif(not shutil.which("man"), reason="no man on this box")
def test_live_man_page_parses():
    text = options.read_man("ls")
    if not text:
        pytest.skip("ls has no man page here")
    s = parse_man("ls", text)
    assert s.option("-a") and s.option("-l") and len(s.options) > 30


# clap's long --help, as fd prints it: no man page, so this is the only table.
FD_HELP = """\
A program to find entries in your filesystem

Usage: fd [OPTIONS] [pattern] [path]...

Arguments:
  [pattern]
          the search pattern

Options:
  -H, --hidden
          Include hidden directories and files in the search results. Files are hidden if
          their name starts with a dot.

  -d, --max-depth <depth>
          Set maximum search depth (default: none)

  -v, --verbose...
          Say more
"""


def test_help_text_gives_flags_synopsis_and_positionals():
    s = options.parse_help("fd", FD_HELP)
    assert s.source == "help"
    assert s.synopsis == ("fd [OPTIONS] [pattern] [path]...",)
    assert [p.name for p in s.positionals] == ["pattern", "path"]
    assert s.option("--max-depth").arg == "<depth>"
    assert s.option("-H").desc.startswith("Include hidden")


def test_a_repeatable_flag_drops_its_dots():
    assert parse_head("-v, --verbose...").flags == ("-v", "--verbose")


def test_usage_continuation_lines_are_more_synopses():
    s = options.parse_help("bat", "Usage: bat [OPTIONS] [FILE]...\n       bat <COMMAND>\n\n"
                                  "Options:\n  -p, --plain\n          Plain style\n")
    assert s.synopsis == ("bat [OPTIONS] [FILE]...", "bat <COMMAND>")


def test_scan_falls_back_to_help_only_without_a_man_page(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "read_man", lambda n: "")
    monkeypatch.setattr(options, "read_help", lambda n: FD_HELP if n == "fd" else "")
    r = options.scan(["fd", "nothing"], tmp_path, workers=1, log=lambda s: None)
    assert list(r["specs"]) == ["fd"] and r["specs"]["fd"]["source"] == "help"
    assert r["missing"] == ["nothing"] and r["from_help"] == 1
    r = options.scan(["fd"], tmp_path, workers=1, help_fallback=False, log=lambda s: None)
    assert r["specs"] == {} and r["missing"] == ["fd"]


def test_read_help_never_runs_a_skip_listed_name(monkeypatch):
    from jiffy.corpus import helpscan
    ran = []
    monkeypatch.setattr(helpscan, "run_argv", lambda argv: ran.append(argv) or ("ok", ""))
    assert options.read_help("sudo") == "" and ran == []


@pytest.mark.parametrize("arg,desc,want", [
    ("TYPE", "Display only selected information: MEMORY, UTILIZATION, TEMPERATURE, POWER. Flags can be combined.",
     ("MEMORY", "UTILIZATION", "TEMPERATURE", "POWER")),
    ("<THEME>", "Select color theme [possible values: dracula, github-dark, nord]", ("dracula", "github-dark", "nord")),
    ("{auto,always,never}", "colorize", ("auto", "always", "never")),
    ("WHEN", "WHEN can be ‘always’ (default if omitted), ‘auto’, or ‘never’; more info below",
     ("always", "auto", "never")),
    ("target", "This is one of the following values: ACCEPT, DROP, RETURN, a target extension (see X)",
     ("ACCEPT", "DROP", "RETURN")),
])
def test_listed_reads_the_values_a_page_lists(arg, desc, want):
    assert options.listed(arg, desc) == want


@pytest.mark.parametrize("arg,desc", [
    ("query-gpu", "Pass comma separated list of properties. e.g. --query-gpu=pci.bus_id,persistence_mode."),
    ("sym=value", "Define the symbol sym to be value before assembling"),
    ("n", "Set the number of jobs."),
])
def test_listed_finds_nothing_in_prose(arg, desc):
    assert options.listed(arg, desc) == ()


def test_values_survive_the_cache_and_old_caches_load(tmp_path):
    o = Option(("--color",), "WHEN", True, "=", "colorize", ("always", "auto", "never"))
    s = options.Spec("x", ("x",), (), (o,), "man")
    assert options._from_json(options._to_json(s)) == s
    old = options._to_json(s)
    del old["options"][0]["values"]
    assert options._from_json(old).options[0].values == ()


# nvidia-smi's layout: indented `WORD OPTIONS` subsections under OPTIONS.
SMI = """\
OPTIONS
   GENERAL OPTIONS
   -h, --help
       Print usage information and exit.

   LIST OPTIONS
   -L, --list-gpus
       List each of the NVIDIA GPUs in the system.

   -B, --list-excluded-gpus
       List each of the excluded NVIDIA GPUs in the system.

   QUERY OPTIONS
   -q, --query
       Display GPU or Unit info.

   -d TYPE, --display=TYPE
       Display only selected information: MEMORY, UTILIZATION, TEMPERATURE

   SELECTIVE QUERY OPTIONS
   --query-gpu=
       Information about GPU.

RETURN VALUE
   -z
       not an option section
"""


def test_a_mode_group_ties_its_flags_to_the_flag_that_leads_it():
    s = parse_man("nvidia-smi", SMI)
    needs = {o.short: o.needs for o in s.options}
    assert needs["-d"] == "-q"                     # under QUERY OPTIONS, led by --query
    assert needs["-q"] == "" and needs["-B"] == ""  # LIST is led by --list-gpus: no mode
    assert needs["-h"] == "" and needs["--query-gpu"] == ""


def test_needs_survives_the_cache_and_old_caches_load():
    o = Option(("-d",), "TYPE", needs="-q")
    s = options.Spec("x", ("x",), (), (o,), "man")
    assert options._from_json(options._to_json(s)) == s
    old = options._to_json(s)
    del old["options"][0]["needs"]
    assert options._from_json(old).options[0].needs == ""
