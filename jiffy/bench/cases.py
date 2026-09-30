"""The 60 p12 cases. `gold` is the ONE normaliser for the `cmd` field.

`cmd` is a str in 15 cases and a list (any-of) in 45. Comparing against a str only
scores 0.233 on a perfect retriever — that number is the signature of this bug.
Provenance: bench/cases/p12_cases.json is a copy of ~/jev-probes/p12_cases.json.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "bench" / "cases" / "p12_cases.json"
RESULTS_DIR = ROOT / "bench" / "results"
LABELS_PATH = ROOT / "bench" / "labels" / "lines.jsonl"


def gold(case: dict) -> list[str]:
    g = case["cmd"]
    return g if isinstance(g, list) else [g]


def load_cases(path: Path | str | None = None) -> list[dict]:
    return json.loads(Path(path or CASES_PATH).read_text())
