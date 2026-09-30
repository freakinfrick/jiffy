"""Line-level grading: does the SHIPPED LINE do what was asked, not just the command?

Top-1 only checks the command name, so `fd -e {{txt}}` for "list python files in
the home directory" counts as a hit. `line_ok` grades the line itself.

The grader is not the pipeline's critic. It sees only the request and the final
line (no candidate list, no `does` text from the corpus), and its wording is its
own, so the pipeline cannot pass by agreeing with itself. The judge is the same
model family, so the two are correlated; they are not independent.
"""
from __future__ import annotations

import json
from pathlib import Path

from jiffy.fill.tokens import PLACEHOLDER
from jiffy.judge.base import JudgeAdapter

LINE_THRESHOLD = 0.5

# Two narrow questions instead of one broad one. Asked as one question ("does it do
# exactly the request, placeholders aside when the request left them open"), the
# grader failed `wget {{url}}` for "download a file from a url" and passed `npm
# whoami` for "what is my username"; rewording it only moved the errors (17/22 and
# 16/22 against hand labels, 2026-09-21). Split, it agreed on 18/22.
FIT_INSTRUCTIONS = (
    "A person typed `user_request` into a shell assistant on a Linux machine, which proposed `line`. `{{...}}` "
    "marks a blank they fill in later; ignore blanks. Is this a tool meant for this job, and do its options "
    "do what they asked, including every restriction they stated (file types, counts, sizes, depth, recursion, "
    "which items)?")
FIT_YES = "Right tool; the options do the request and honour its restrictions."
FIT_NO = "Wrong tool, or the options do something else or drop a stated restriction."

BLANK_INSTRUCTIONS = (
    "A person typed `user_request`. The proposed command has a blank `blank` (in `line`) they must fill. "
    "Did `user_request` already state the value that belongs in this blank (a specific number, name, path, "
    "type, host, or word)?")
BLANK_YES = "The request states that value; the blank should have been filled."
BLANK_NO = "The request leaves that value open; a blank is fair."


def load_labels(path: Path | str | None) -> dict[tuple[str, str], int]:
    """(request, line) -> 1 / 0 from a labels file (`bench/labels/lines.jsonl`).

    A labelled line is graded by its label, with no judge call: the same line
    always gets the same grade, and costs nothing. `unsure` labels are left out,
    so those lines go to the judge. A missing file is no labels.
    """
    p = Path(path) if path else None
    if p is None or not p.exists():
        return {}
    out: dict[tuple[str, str], int] = {}
    for ln in p.read_text().splitlines():
        if ln.strip():
            r = json.loads(ln)
            if r["label"] in (0, 1):
                out[(r["q"], r["line"])] = int(r["label"])
    return out


def unfilled(line: str) -> int:
    """`{{...}}` tokens still in the line. Deterministic, no judge."""
    return len(PLACEHOLDER.findall(line or ""))


def grade_line(judge: JudgeAdapter, query: str, line: str) -> float:
    """P(the line does exactly the request), as min(fit, 1 - worst stated-but-unfilled blank).

    >= LINE_THRESHOLD iff the tool fits AND no blank holds a value the request
    already gave. An empty line is 0 without a call. A failed blank question
    raises, so the harness records the case as ungraded, never as a pass.
    """
    if not (line or "").strip():
        return 0.0
    fit = float(judge.noul({"user_request": query, "line": line}, FIT_INSTRUCTIONS, yes=FIT_YES, no=FIT_NO))
    blanks = PLACEHOLDER.findall(line)
    if not blanks:
        return fit
    jobs = [(lambda b=b: judge.noul({"user_request": query, "line": line, "blank": "{{" + b + "}}"},
                                    BLANK_INSTRUCTIONS, yes=BLANK_YES, no=BLANK_NO)) for b in blanks]
    stated = judge.batch(jobs)
    for p in stated:
        if isinstance(p, BaseException):
            raise p
    return min(fit, 1.0 - max(float(p) for p in stated))
