# Bench

`jiffy bench` runs the pipeline over bench cases and reports top-1 (was the command
right), recall after each stage, latency and USD. With `--line` it also grades the
shipped line itself (`line_ok`). Any claim that a change helps should come from this
command.

## Case sets

| File | Cases | Use |
|---|---|---|
| `bench/cases/fresh_cases.json` | 106 | **Held-out.** Accuracy is quoted from this set only, and only as aggregate numbers. Don't read individual failures here to write fixes. |
| `bench/cases/p12_cases.json` | 60 | Development. It was tuned against for months: treat it as a training set. |
| `bench/cases/line_cases.json` | 100 | `--split dev` (50) to develop line quality, `--split heldout` (50). |

A case is `{"q", "cmd"}`; a list `cmd` means any entry counts, which is how cases
cover distros that ship different tools. Never close a miss with a recipe or rule
that copies a bench question.

## Run it

```
cd ~/jiffy
python3 -m jiffy bench --cases bench/cases/fresh_cases.json --all --dry          # cost estimate, no network
python3 -m jiffy bench --cases bench/cases/fresh_cases.json --all --line         # paid: ~$0.93, ~4 min
```

| Flag | Meaning |
|---|---|
| `--cases FILE` | cases file, repeatable (default `bench/cases/p12_cases.json`) |
| `--split dev\|heldout` | only cases tagged with that split |
| `--line` | also grade each shipped line with Jev (`line_ok`, `unfilled_rate`) |
| `--all` | every case; default is the first 5, and a paid run over 10 cases needs `--all` |
| `--limit N` | first N cases |
| `--dry` | stub judge, estimated cost, no network |
| `--usd-cap USD` | override the daily cap for this run; a run whose estimate would pass the cap is refused |
| `--json` | print JSON |

A live run saves to `bench/results/pipeline-<date>[-line].json` and overwrites an
earlier run from the same day. To keep the repo clean, run from a script that calls
`jiffy.bench.harness.bench(..., save=False)`.

A run is **void** if `degraded_per_node` is not empty. Causes seen so far: the
TypeSafe account ran out of credits (HTTP 402), and a stage hit the 8 s timeout
while the API was slow.

## Labelled lines

`--line` grades a shipped line by its label in `bench/labels/lines.jsonl` when the
(request, line) pair is there, with no judge call; other lines go to the Jev grader.
Each result row says which (`graded_by`: `label` or `judge`), and the line summary
counts `by_label`. `--no-labels` grades everything with Jev.

The 195 labels (2026-09-28) cover every line the critic or compose produced on 50
held-out cases in two runs. They were written by Claude (`"by"`), blind to Jev's
grades and to which code produced each line, with a one-line reason each; 9 are
`unsure` and go to Jev. Against them the Jev grader agreed on 149 of 186 and leaned
lenient: it passed 29 lines labelled wrong and failed 8 labelled right. The deck and
Jev's grades sit beside the labels (`lines-2026-09-28.json`, `.jev.json`); the human
labels started on that deck are in `lines-2026-09-28.labels.jsonl` (13 cards).
New lines are labelled the same way before they count.

## Noise floor

Jev does not answer byte-identical requests identically. On the held-out set, two
runs of the same code differ on 9 to 10 of 106 shipped lines, and top-1 moves by
up to 3 cases. So run each side twice: a change counts only when the gap is larger
than the gap between two runs of the same code.

## Portability

`bench/docker/run.sh all` builds Debian 12, Ubuntu 22.04 and Ubuntu 24.04 images,
installs jiffy from the repo, runs `jiffy index` in each, and runs the tests. It
needs no key. `JEV=1 bench/docker/run.sh ubuntu:22.04` also runs the paid held-out
bench inside the container.

## Tests

`python3 -m pytest tests -q`. Tests run against an empty
config directory (`tests/conftest.py`), so this machine's `denylist.txt` and config
never decide a result.
