# jiffy

A smart terminal assistant: CLI commands on demand, from plain English.

jiffy draws on the commands installed on your machine and their documentation:
man pages, `--help` output and tldr examples. It indexes the machine it runs on.
TypeSafe's Jev model makes every judgement (which command, which example, which
flags, which value fills a blank). Code writes every string. Your shell history
only offers values, such as paths you have used before. It never ranks commands.

jiffy is a 2026 take on Clippy. It aims to help you learn to operate any CLI
environment, not only to autofill a line: every flag in the line on offer comes
with what its own page says it does.

Spotlight for the terminal: you type what you want, and jiffy autofills a shell
command, keeping your request as a trailing comment on the line.

## Example

Real output from `jiffy --explain "list markdown files in my home directory"` on
the development box (2026-09-23):

```
ls ~/*.md  # list markdown files in my home directory
'list markdown files in my home directory'
  retrieve     retrieve  jev         0->102      929ms $0.0080  indexed=5026 chunks=34 failed=0 top=['ls', 'vdir', 'dir']
  pick         pick      jev       102->102      260ms $0.0004  top=[('find', 0.576), ('ls', 0.283), ('fd', 0.141)]
  invoke       invoke    jev       102->102      352ms $0.0001  command=find example=1 p=1.0 line=find {{path/to/directory}} -name '{{*.ext}}'
  critic       critic    jev       102->102      239ms $0.0001  failed=0 installed=True graded=[('find', 0.4), ('ls', 0.12), ('fd', 0.13)] fell_back=False shipped=find line=find {{path/to/directory}} -name '{{*.ext}}' below_threshold=True
  compose      compose   jev       102->102     2208ms $0.0009  fired=True failed=0 tried=[('find', "find ~ -type files -name '*.md'", 0.29, ['-type', '-name'], 13), ('ls', 'ls ~/*.md', 0.74, [], 10)] shipped=True line=ls ~/*.md p=0.74 incumbent_p=0.4
  fill         fill      jev       102->102       15ms $0.0000  filled=1 unresolved=0 values=path/to/file=~/*.md
  gate         gate      jev       102->102      251ms $0.0000  tier=CLEAN destructive=0.02
  total 4254ms $0.0094
```

The critic scored tldr's `find` example 0.4, below its 0.5 bar. So compose built
lines from man-page flags, and `ls ~/*.md` won. [docs/architecture.md](docs/architecture.md)
explains each stage. The run cost just under a cent; see [Backend and cost](#backend-and-cost).

## Install and launch

Debian 12, Ubuntu 22.04 or Ubuntu 24.04, Python 3.10+, `man-db`, and a TypeSafe
Jev API key in `~/.config/typesafe/api_key`. Without the key, `jiffy` exits with a
one-line message saying where to put it. `jiffy index`, `jiffy config` and
`jiffy bench --dry` work without it.

`jiffy index` indexes the machine it runs on: every installed command with a
section 1/8 man page, the PATH binaries (a binary with no man page gets its
`--help`, run sandboxed), the shell builtins, and examples from the pinned tldr
release (v2.3, downloaded once and hash-checked; `--tldr-zip PATH` takes an offline
copy). The index lives in `~/.local/share/jiffy/corpus/machine.json`, never in the
repo or the package, and jiffy warns when the OS it was built on changes.

```
python3 -m venv ~/venv-jiffy
~/venv-jiffy/bin/pip install git+https://github.com/freakinfrick/jiffy
~/venv-jiffy/bin/jiffy index
~/venv-jiffy/bin/jiffy
```

jiffy is not on PyPI (the name there belongs to an unrelated package). From a
clone without installing: `cd jiffy && python3 -m jiffy`. One answer,
printed: `jiffy "rename a file"` (add `--explain` for the per-stage trace).

`jiffy index --options` parses the man page of every installed command into a flag
table (`options.json`: flags, argument, description, positionals from the
synopsis). A command with no man page (fd, bat, uv) falls back to its `--help`
text, run in the same sandbox, never for a skip-listed name. `--no-help-fallback`
reads man pages only and runs nothing. The tables feed both line composition and
the flag notes under the prompt.

## Keys

| Key | Does |
|---|---|
| TAB | Accept the ghost text into the line. Does **not** run it. |
| ENTER | Search the line as typed; once an answer is ready, run it, subject to the gate. |
| typing | On a `{{token}}`, the keystroke replaces the whole token — the token is the field, so you just type the value. Anywhere else, ordinary insert. |
| UP / DOWN | Move through the candidate list. Before TAB this only moves the highlight and the ghost; after TAB it swaps the line itself. |
| ESC | Clear back to empty. Also backs out of a pending CONFIRM. |

^C abandons the line; ^D exits. History is on ^P / ^N and ^R, where
prompt_toolkit puts it — jiffy no longer binds those.

A command whose output does not end in a newline keeps its last line: jiffy
returns the cursor to column 0 before the next prompt draws, without inventing
a blank line when the output ended cleanly.

## Placeholders

A documented line arrives full of `{{...}}` tokens. jiffy fills what your request
already answers, before the gate sees the line. For "how much storage does the
home directory take":

```
sudo btrfs {{[f|filesystem]}} du {{[-s|--summarize]}} {{path/to/directory}}
sudo btrfs f du -s ~
```

`{{[f|filesystem]}}` and `{{[-s|--summarize]}}` are tldr *spellings*. Both forms are
already correct, so jiffy writes the short one without asking anyone.
`{{path/to/directory}}` is a *value*. Code proposes what it could be: `.`, `~`,
`..`, `/`, anything you typed that looks like a path, directories in your home
or in `/` that you named, paths from your shell history (`~/.bash_history`,
read-only), and so on. Jev picks one, or none. Here it picked `~` for "home
directory". Nothing in jiffy maps phrases to places. File types come from
`/etc/mime.types` on your machine ("python files" → `*.py`).

Nothing is invented. When nothing fits, the token stays a token, drawn in red, and
ENTER stays blocked until you fill it, which is just typing:

```
» tree -L {{num}}        cursor parks on the token
» tree -L 2              type 2; the braces go with it
```

TAB walks to the next token, typing fills it, and a line with no tokens left runs.
The colours say where each value came from: your own words read as plain text,
anything inferred from this box is grey, a default (`head -n 10`) is blue, and an
unresolved token is the only red on screen.

Filling happens **before** the gate, which is the point: `rm -rf {{path}}` and
`rm -rf ~` are different commands, and the gate grades the one that will run. Jev
is only ever asked which of the strings code built fits. It never writes a value.

## Candidates

Once a run lands, the alternatives sit directly under the line you are typing, best
first. The line you type on never moves: nothing is drawn above it. The run's
metadata sits on the right of that same line: the judge that answered, the time,
the cost, and your place in the list (`jev · 2.1s · $0.0094 · 1/9`), plus
`degraded` if a stage fell back.

ENTER erases the list before the shell writes anything, so the command's output
starts on the line under your command, and the scrollback keeps a clean record:
each command you ran, then its output, with no candidate lists or timings left
behind.

Six rows at a time; the window follows the selection and names what it hides. A
line the gate denies is struck through in the list, because ENTER will refuse it.
Alternates are filled by code alone, from what you typed. Only the winning line
gets Jev's picks.

## What each flag does

Under the candidates, every flag in the line on offer gets one row, in its own
man page's (or `--help`'s) words. From a real run, with the list cut short:

```
» show the directory tree 2 levels deep   → tree -L 2
  │ ▸ tree -L 2
  │   tre -d
  │   +7 more

  -L 2  Max display depth of the directory tree
```

The rows describe the selected candidate. After TAB, they follow the line as you
edit it. Clusters are split (`-lah` is three rows), and a flag's argument is shown
with it. All of this is code: no judge call, and no command is run. A flag the
tables do not know gets no row rather than a guess. The tables come from
`jiffy index --options`; without them the section is simply absent.

## Recipes

Some requests need a pipeline or an idiom that no tldr page documents, and compose
cannot build it from one command's flags: your public IP (`curl -s
https://ifconfig.me`), the biggest folders here (`du -sh {{path/to/directory}}/* |
sort -rh | head -n {{n}}`), the most common lines in a file (`sort | uniq -c | sort
-rn`). jiffy ships these as recipes in `jiffy/corpus/recipes/*.md`, in tldr's page
format, placed at the front of their command's page.

Recipes are for idioms only. A recipe that rewords an example tldr already has, or
copies a question someone might ask, does not belong there: it answers one phrasing
and teaches nothing. `tests/corpus/test_recipes.py` checks that every shipped page
parses.

## When a command is not installed

Of the lines the critic passes, the first one whose command is on this box
ships: `ipinfo myip` no longer beats `curl -s https://ifconfig.me`. When
nothing installed passes, the line still shows, with a row that says so:

```
» show system info with neofetch   → neofetch
  neofetch is not installed here
```

## Where commands run

In your home directory, by default — `tree -L 2` shows `~`, not whichever repo
you launched jiffy from. The same directory is what "here" means when a path is
filled. Set `workdir = "."` in `~/.config/jiffy/config.toml` to run from the
launch directory instead, or `JIFFY_WORKDIR=/some/where`.

## When jiffy searches

On ENTER, never on a keystroke. You type freely, then press ENTER when the line is
worth answering. Nothing is drawn while you are still typing, and nothing is spent.

`… thinking` and its horizon bar appear when a run actually starts. The bar's
clock counts the run only (~2 s at the median).

Typing again cancels. The candidate list, the ghost and the horizon clear on the
keystroke, and the run itself is cancelled between stages, so a superseded query
stops paying at the next stage boundary. The stage already in flight cannot be
killed, so the waste is bounded by one stage.

## Safety gate

Every line passes a deterministic pattern-matching gate before the shell sees it.

| Tier | Meaning |
|---|---|
| 3 DENY | Never reaches the shell. |
| 2 CONFIRM | Red banner naming the reason; a second ENTER runs it. |
| 1 NOTE | Yellow tag; runs normally. |
| 0 clean | Runs. |

Checked: `rm -rf /` DENY (`builtin:rm-rf-root`), `bash <(curl -fsSL URL)` DENY
(`builtin:shell-fetch-substitution`), `rm -rf ./build` CONFIRM
(`builtin:rm-recursive`), `sudo apt install jq` CONFIRM (`builtin:sudo`), `ls -la`
clean.

**A DENY line never reaches the shell and no key overrides it.** Jev can raise a
tier (clean to NOTE to CONFIRM) but never lower one and never reach DENY. A judge
failure cannot become a shell failure.

The built-in rules hold on any machine. Rules about *your* machine go in
`~/.config/jiffy/denylist.txt`: one regex or literal per line, `#` starts a comment,
every match is DENY. Entries are added to the built-in set and can never remove
from it; there is no allow or negation syntax. A line that fails to compile, or
that could backtrack catastrophically, is warned about and matched literally, so it
still denies its own text. For example, to keep a GPU someone else uses and a
local database off limits:

```
# GPU 1 is shared: off limits
CUDA_VISIBLE_DEVICES\s*=\s*["']?[0-9,]*(?<![0-9])1(?![0-9])
# port 5432 is the local Postgres: never touch it
(?<![0-9])5432(?![0-9])
```

## Backend and cost

One pipeline and one judge: Jev, the paid TypeSafe API. Each query costs
~$0.0087 and takes ~2 s at the median. On the 106 held-out bench cases, the right
command comes first 0.83-0.86 of the time, and 0.85-0.87 of the shipped lines are
graded correct ([docs/bench.md](docs/bench.md)).

jiffy keeps its own spend ledger (`~/.local/share/jiffy/ledger.json`) and stops
paying past `usd_cap` for the day (default $1.00, about 115 queries): each stage
then degrades and the gate still runs. Settings live in `~/.config/jiffy/config.toml`
(written on first run, never rewritten). `JIFFY_JUDGE=stub` or `--judge stub`
swaps in an offline test judge whose answers are hashes, not judgements.

Footer: `<judge that answered> · <time> · $<cost> · <i>/<n>`, plus `degraded` when
a stage timed out and fell back. `jiffy --explain` prints the per-stage trace
(stages, candidates in/out, latency, cost, what each decided). `jiffy config` shows
the resolved paths and settings.

## `jiffy learn`

An explicit, idempotent, user-initiated phase that produces a version-controlled
artifact from accepted autofills. Nothing learns in the background. **All persistence
(history, accepted-autofill records, aliases) is off by default** under `[persistence]`
in the config.

Status in this build: `jiffy learn` is wired to `jiffy.learn.main`, and that module
does not exist yet. Today the command exits 2 with a message saying persistence is
off by default.

## More

- [docs/architecture.md](docs/architecture.md): the pipeline stage by stage, fill, measured numbers.
- [docs/bench.md](docs/bench.md): case sets, reproducing the numbers, the noise floor, what it costs.
- [SPEC.md](SPEC.md): the interaction contract.
- Tests: `python3 -m pytest tests -q` (no API key or network needed).

## License

MIT, see [LICENSE](LICENSE). Command examples come from
[tldr-pages](https://github.com/tldr-pages/tldr) (CC BY 4.0), downloaded by
`jiffy index` and never redistributed in this repo.
