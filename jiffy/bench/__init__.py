"""The honesty instrument: score the pipeline over the bench cases.

    from jiffy.bench import main; main(["--cases", "bench/cases/fresh_cases.json", "--dry"])   # `jiffy bench ...`
"""
from __future__ import annotations

import argparse
import json
import sys

from .cases import LABELS_PATH, gold, load_cases
from .harness import BenchRefused, bench, DEFAULT_LIMIT
from .report import render

__all__ = ["bench", "main", "gold", "load_cases", "render", "BenchRefused"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jiffy bench", description="Score the pipeline over the bench cases.")
    ap.add_argument("--cases", action="append",
                    help="cases file, repeatable (default bench/cases/p12_cases.json)")
    ap.add_argument("--split", choices=("dev", "heldout"), help="only cases tagged with this split")
    ap.add_argument("--line", action="store_true",
                    help="also grade each shipped LINE with jev (paid, ~$0.0001/case)")
    ap.add_argument("--no-labels", action="store_true",
                    help="with --line: grade every line with the judge, ignoring bench/labels/lines.jsonl")
    ap.add_argument("--limit", type=int, help=f"first N cases (default {DEFAULT_LIMIT})")
    ap.add_argument("--all", action="store_true", help="all cases; required for a big run with a paid judge")
    ap.add_argument("--dry", action="store_true", help="estimate cost only: no network, no paid calls")
    ap.add_argument("--usd-cap", type=float, help="override jiffy's usd_cap for this run")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)

    def prog(i, n, eta):
        if n >= 10 and (i == 1 or i % 10 == 0):
            print(f"  {i}/{n} done, ETA {eta / 60:.1f} min", file=sys.stderr, flush=True)
    try:
        res = bench(cases_path=a.cases, limit=a.limit, all_=a.all, dry=a.dry,
                    usd_cap=a.usd_cap, progress=prog, line=a.line, split=a.split,
                    labels=None if a.no_labels else LABELS_PATH)
    except BenchRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, ensure_ascii=False) if a.json else render(res))
    return 0
