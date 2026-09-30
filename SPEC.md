# jiffy — build spec v3 (interaction contract + one pipeline)  2026-09-23

## Product
`~/jiffy` — Spotlight for the terminal. Type natural language, get an autofilled
shell command with your request preserved as a trailing comment.

## Interaction contract
- Start empty; first keystroke opens the prompt.
- Ghost text shows the top candidate inline.
- TAB   — accept the autofill into the line, do NOT run.
- ENTER — search the line as typed; once an answer is ready, run the assembled
  line (subject to the gate).
- UP/DOWN — move through the candidate list, which is rendered under the prompt
  (best first, windowed to 6 rows). Unaccepted, this moves the highlight and the
  ghost only; accepted, it swaps the line. History stays on ^P/^N and ^R.
- A run starts only when you press ENTER; typing never triggers one. Nothing is
  drawn while a line is merely being typed.
- Any edit cancels: the turn on screen is dropped immediately, and the run in
  flight is cancelled via a token `Pipeline.run` polls between stages, so a
  superseded query stops spending. `Cancelled` is a BaseException so the
  degrade-and-continue path cannot turn an abandoned run into a half-answer.
- Assembled form, trailing comment:
      find . -name '*.log' -size +100M  # find big log files
- Lines carry `{{placeholder}}` tokens from the corpus. The `fill` stage resolves
  what the request answers: alternations to their short spelling, values by a Jev
  pick (one or none) among proposals code built from the request, cwd, env,
  history and `/etc/mime.types`. It runs BEFORE the gate, so the gate grades the
  line that will actually run. Nothing is invented: a token with no proposal, or
  that the judge leaves, stays a token, is drawn in red, and is never silently run.
  Accepting a line with placeholders puts the cursor on the first one, and a
  printable keystroke there REPLACES the whole token: filling one is typing it.
  TAB walks to the next.
- The input line never moves: nothing renders above it. The candidate list is
  mounted in the prompt layout directly beneath the input (not `bottom_toolbar`,
  which pins to the screen bottom) and is erased on ENTER, so output begins on
  the next line. The footer is the rprompt, hidden once the line is done. One
  two-space margin, a dim rail down the list. An idle prompt is one line tall.
- Commands run in `workdir` (default `~`); the TUI chdirs there once at startup
  so the shell and the fill sources share one notion of "here".
- After a command runs, the cursor is returned to column 0 so output that ended
  mid-line is not overwritten by the next prompt.

## Frontend
Python + prompt_toolkit. Real readline semantics, native autosuggestion,
native key bindings. No full-screen widget layer.

## Gate — four tiers, deterministic floor
    tier 3  DENY     never reaches the shell. No key overrides it.
    tier 2  CONFIRM  red banner naming the reason; second ENTER runs.
    tier 1  NOTE     yellow tag; runs normally.
    tier 0  clean    runs.
DENY set = built-in catastrophic list + `~/.config/jiffy/denylist.txt` (user
additions merge, never subtract). Built-ins hold on any machine: `rm -rf /`,
`mkfs.*`, `dd of=/dev/sd*`, fork bombs, `curl … | sh`, `bash <(curl …)`,
`chmod -R 777 /`, and the like. Rules about one machine (its GPUs, its services)
live in that machine's denylist.txt, never in the built-ins.
Pattern matching only. The judge MAY raise a tier (its destructive Noul can
promote clean→NOTE→CONFIRM) but MAY NEVER lower one, and may never reach DENY.
A model failure must not become a shell failure.

## Pipeline — one, fixed
    retrieve  tournament: every indexed `name: description`, chunks of 150, one
              Jev choice per chunk in one batch, top 3 of each go on.
    pick      one Jev choice over the finalists (what each does, first 3 uses).
    invoke    one Jev choice over the winner's documented examples -> the line.
    critic    Jev yes/no on the line; below 0.5, the next candidates' best lines;
              the first installed one that clears ships.
    compose   only when nothing cleared: Jev yes/no per man-page flag, code
              writes the line, graded filled; ships only if it beats the incumbent.
    fill      code proposes, one Jev pick per token (or none), code writes.
    gate      code, four tiers, terminal. Never skippable.
- Code writes every string; the judge only chooses among options code built.
- Degrade, never hard-fail: a stage that times out (8 s), errors or hits the
  spend cap keeps the previous stage's result; the trace marks it `degraded`.
- Every run emits a trace (stage, candidates in/out, latency, cost, decision);
  `jiffy --explain` prints it and the footer names the judge that answered.
- Budget: jiffy's own ledger and daily `usd_cap` ($1.00 default).

## Judge backend
- `jev`: TypeSafe API, paid, key at `~/.config/typesafe/api_key`. REQUIRED:
  without a key `jiffy` exits with one line saying where to put it.
- `stub`: offline and deterministic, for tests and `jiffy bench --dry`.
- One adapter interface (`JudgeAdapter`). A local model drops in behind it later
  without touching retrieval, fill, the pipeline or the gate.

## Corpus — this machine
`jiffy index` builds the index from the machine it runs on: `man -k` sections
1/8, PATH binaries (sandboxed `--help` when there is no man page), shell
builtins, and examples from a pinned, hash-checked tldr release for installed
commands. `/etc/os-release` is recorded; a change warns. Recipes
(`jiffy/corpus/recipes/*.md`) add idioms tldr lacks, never rewordings of a
bench question. Generated data never enters the repo or the wheel.

## Persistence — everything off by default
No file written outside `~/.config/jiffy/` and the corpus cache unless a
setting is toggled on. Opt-in: command history, accepted-autofill records,
aliases. Local reranking from accepted autofills is NOT a background learner:
it is an explicit, idempotent, user-initiated phase (`jiffy learn`) producing a
version-controlled artifact. Running it twice on the same inputs yields the
same output, byte for byte.

## Bench — the honesty instrument
`jiffy bench` scores the pipeline: top-1, recall per stage, latency, USD, and
with `--line` whether the shipped line does the request. Accuracy is quoted only
from the held-out `bench/cases/fresh_cases.json`, from two runs per side (Jev is
not deterministic). A run with any degraded stage is void.

## Hard constraints
- Never close a bench miss with text that copies or paraphrases a bench question.
- Never invent a value; never run a line that still holds a token.
- A machine's own limits (a service it must not kill, a GPU it must not touch)
  are that machine's rules. Its `~/.config/jiffy/denylist.txt` enforces them;
  the built-in rules do not.

## Deliverable
Runnable `~/jiffy` with README (install, key contract, cost), the TUI, the
pipeline, the four-tier gate wired before execution, the bench with a held-out
set and a three-distro Docker matrix, and tests for gate tiers, fill, assembly
and pipeline determinism.

---

## Module map

Which package owns which unit. A unit writes inside its own package and nowhere
else; shared vocabulary lives in `jiffy/types.py` and is imported, never
re-declared.

| Package | Owns | Key entry points |
|---|---|---|
| `jiffy/types.py` | Shared frozen dataclasses: `Doc`, `Example`, `Candidate`, `Assembled`, `Tier`, `GateVerdict`, `NodeTrace`, `Trace`, `Answer`. Contract-owned; downstream units import, never edit. | — |
| `jiffy/config.py` | `~/.config/jiffy/config.toml` load + defaults + env overrides. Contract-owned. | `load()` |
| `jiffy/corpus` | The machine index (`machine.py`: man -k, PATH, builtins, pinned tldr), `--help` scan, man-page option tables, recipes, the cache under `~/.local/share/jiffy/corpus/`. | `index.load()` |
| `jiffy/judge` | Backend adapters behind `JudgeAdapter` (`base.py`, contract-owned): `jev` (paid API) and `stub`, plus the registry. Owns HTTP, caching, the USD ledger against jiffy's own cap. | `get(name)`, `register(name)` |
| `jiffy/gate` | The four deterministic tiers, built-in denylist + `~/.config/jiffy/denylist.txt` merge, judge-may-raise-never-lower rule. Terminal stage, never skippable. Produces `GateVerdict`. | `evaluate(line) -> GateVerdict` |
| `jiffy/graph` | The pipeline (`pipeline.py`: seven stages, per-stage timeout, degradation, `Trace` emission), compose's code half (`compose.py`), command assembly into `Assembled`. | `Pipeline(docs).run(query) -> Answer` |
| `jiffy/fill` | Token classification (`tokens.py`), value proposals (`sources.py`), `resolve` (one batched pick, code writes). | `resolve(line, query, sources, pick, choices)` |
| `jiffy/tui` + `jiffy/cli.py` | prompt_toolkit frontend (ghost text, TAB accept, ENTER run, the UP/DOWN candidate list, placeholder cursor), the confirm banner, `--explain` printing, and the `jiffy` / `jiffy index` / `jiffy learn` / `jiffy bench` argument surface. | `main()` |
| `jiffy/bench` | `jiffy bench` over `bench/cases/*.json`: top-1, recall per stage, latency, USD, `--line` grading. | `bench()` |
| `tests/` | `test_contract.py` is contract-owned. Gate-tier, fill, assembly and pipeline tests belong to their owning units. `tests/conftest.py` gives every test an empty config dir. | — |
