# Cases

`p12_cases.json` is a verbatim copy of `~/jev-probes/p12_cases.json` (60 hand-written
queries, read-only source; copied 2026-09-20 so jiffy is self-contained).

Shape: `{"q": "...", "cmd": "ls"}` (15 cases) or `{"q": "...", "cmd": ["grep","rg","ag"]}`
(45 cases). A list means ANY entry is a correct answer. JSON has no comments, hence
this file; the single normaliser is `jiffy.bench.cases.gold`.

`line_cases.json` (added 2026-09-21): 100 hand-written queries for LINE-level grading
(`jiffy bench --line`). Shape: `{"q", "cmd", "type", "split"}`. `type` is the failure it
probes: `combine` (flags spread over two examples), `args` (argument shape tldr lacks),
`values` (a value that must come from the request), `pipe` (needs two commands),
`retrieve` (hard-to-retrieve command). 20 per type, alternating `dev`/`heldout`; tune
on dev only. p12 cases carry no split and count as `dev`.

`fresh_cases.json` (added 2026-09-23): 106 held-out queries written without looking at
the corpus, after p12 had been tuned on long enough to act as training data. Shape:
`{"q", "cmd", "area", "split": "heldout"}`. Git subcommands are listed both as tldr
names (`git stash`) and man names (`git-stash`). Quote accuracy from this set only,
and never tune on it. `tests/bench/test_fresh_cases.py` fails if a case repeats an
older question or a shipped recipe is worded like one. BM25 alone: 27/106 top-1
(0.255), recall@20 75, @120 94 (`bench/results/bm25-2026-09-23-fresh.json`).
With the machine index (2026-09-23, Ubuntu 22.04 dev box) BM25 alone scores 26/106,
recall@20 68, @120 97 (`bench/results/bm25-2026-09-23-fresh-machine.json`). Every
@120 miss has its answer installed; they are vocabulary gaps, not missing commands.
