# Architecture: one pipeline

A query runs through seven stages, in this order, in `jiffy/graph/pipeline.py`:

    retrieve -> pick -> invoke -> critic -> compose -> fill -> gate

Code builds every string: the line, each flag, each filled value. The judge (Jev)
only chooses among options code gives it, or answers yes/no. It never writes text.

## Stages

| Stage | What it does |
|---|---|
| `retrieve` | The tournament. Every indexed command, as `name: description`, split into chunks of 150; one Jev choice per chunk, all in one batch; the top 3 of each chunk go on. On this box that is ~34 chunks and ~100 finalists. |
| `pick` | One Jev choice over the finalists, each shown with what it does and its first three documented uses. |
| `invoke` | One Jev choice over the winner's documented examples (tldr and recipes) gives the line. The next two candidates are asked the same question in the same batch, so the critic finds them cached. |
| `critic` | Jev yes/no: does this line do exactly what was asked, restrictions included? Grades the incumbent and the next two candidates' best lines; ships the first installed line that clears 0.5, else keeps the incumbent, flagged. |
| `compose` | Fires only when nothing cleared the critic. For up to two installed candidates with a man-page option table, code short-lists flags, Jev says yes/no per flag (at most 4 kept), and code writes the line with positionals as `{{?tokens}}` untyped flag arguments closed to their listed values, and each kept flag's mode flag added (`-d` under nvidia-smi's QUERY OPTIONS brings `-q`). The critic's question grades each composed line *filled*; it ships only if it beats the incumbent. |
| `fill` | Replaces `{{tokens}}` (below). Runs before the gate on purpose. |
| `gate` | Terminal, never skippable. Four pattern tiers; a Jev "is this destructive?" probability may raise a tier, never lower one, never to DENY. |

Every stage is timed, metered and degraded the same way (`Pipeline._stage`): a stage
that times out (8 s by default), errors, or hits the spend cap keeps what the stage
before it produced, and its trace row says `degraded`. Only retrieval has nothing to
fall back on. A timed-out stage runs on a sandbox copy of the state, so a late thread
cannot write into a later stage. Cancellation is polled between stages.

## Fill

`jiffy/fill` turns `{{...}}` into values. Code proposes, Jev picks one or none.

- Each token is classified by how tldr named it (`Kind`: DIR, FILE, PATH, PATTERN,
  NUMBER, SIZE, EXT, USER, HOST, PORT, URL, CHIP, DELIM, UNKNOWN; `{{[-s|--summarize]}}` is a
  SPELLING and resolves to the short form with no judge).
- Sources propose values. **Strong** proposals are spans the user typed verbatim
  (quoted strings, paths including `a/b`, file names, globs, digits, sizes, hosts,
  URLs, `called`/`named` X) and facts ($USER, the hostname, a cwd entry the request
  names). **Weak** proposals need a judge: the shell's places `. ~ .. /`, home and
  `/` entries the request names, users from `/etc/passwd`, shell history, plain
  words, dotted names, signed sizes (`+100M`/`-100M`), name globs built from the
  request's words (`I*`, `*_old`, `I*.md`), and extensions from `/etc/mime.types`
  (read at runtime; no file, no extension proposals), and sensor chips from
  `/sys/class/hwmon/*/name` for a CHIP token (`sensors {{chip}}` -> `amdgpu-*`).
  Number words (`second` -> 2, `ten` -> 10) and separator names (`colons` -> `:`) are
  weak and offered only when the request has the word; a DELIM token (`{{delimiter}}`,
  `{{separator}}`) also takes a separator typed as itself (`split on ;`) as strong.
- A compose flag argument of UNKNOWN kind (`nvidia-smi -d {{type}}`) is a
  **closed** blank: it is offered only the values its man page lists
  (`options.listed`: MEMORY, TEMPERATURE, ...), never the request's words, and
  stays a blank when the page lists none.
- One `j.batch` asks one Jev choice per token over its proposals plus `none`.
  Without a judge, or when it fails, only strong proposals fill. That is the path
  the TUI uses for alternates.
- Nothing is invented. A token nothing proposes, or that Jev answers `none`, stays
  a `{{token}}` and the line cannot run until the user types the value. Two narrow
  exceptions: `head`/`tail -n {{n}}` defaults to 10, and an optional `{{?token}}`
  from compose is dropped.

## Judge backends

`jev` (TypeSafe, paid, key in `~/.config/typesafe/api_key`) and `stub` (offline,
deterministic, for tests and `--dry`). Both implement `JudgeAdapter`
(`jiffy/judge/base.py`); a local model can drop in behind the same seam. Only paid
calls are ledgered, against jiffy's own daily `usd_cap` ($1.00 by default).

## Numbers

Held-out set, `bench/cases/fresh_cases.json` (106 cases written without looking at
the corpus). Each side was run twice, because Jev is not deterministic
([bench.md](bench.md)).

| Change | Before | After |
|---|---|---|
| Phase 3: BM25@120 + pick → the tournament pipeline | top-1 0.821, 0.811 | top-1 0.830, 0.849 |
| Phase 4: regex fill → proposals + Jev pick | line_ok 0.774, 0.811 | line_ok 0.868, 0.849 |

Finalist recall after the tournament is 1.000 on the held-out set, so the misses that
remain come from the final pick, not from retrieval. p50 latency is ~2 s and cost is
~$0.0087 per query, so the default cap covers ~115 queries a day.

## Where the index comes from

`jiffy index` builds `~/.local/share/jiffy/corpus/machine.json` from the machine it
runs on: `man -k` sections 1 and 8, the PATH binaries (with `--help` for those that
have no man page), the shell builtins, and examples from a pinned tldr release for
the commands that are installed. `/etc/os-release` goes into the metadata, and jiffy
warns when it changes. Recipes (`jiffy/corpus/recipes/*.md`) are merged in front
of a command's own examples at load time. The wheel ships code and recipes only.
