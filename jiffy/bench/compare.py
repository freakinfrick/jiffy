"""Two saved runs, same cases, the DIFFERENCE. Aggregates hide who wins where.

    jiffy bench --all --json > a.json   (change something)   jiffy bench --all --json > b.json
    python -m jiffy.bench.compare a.json b.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path



def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial p over the discordant cases (ties carry no information)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _usd(r: dict) -> float:
    u = r["usd"]
    return float(u["total"] if isinstance(u, dict) else u)     # older saved results nested it


def diff(a: dict, b: dict) -> dict:
    """Per-case comparison of two results (`b` is the challenger, `a` the incumbent)."""
    ca, cb = a["cases"], b["cases"]
    if [c["q"] for c in ca] != [c["q"] for c in cb]:
        raise ValueError("results cover different cases; compare like with like")
    wins, losses, both, neither = [], [], [], []
    for x, y in zip(ca, cb):
        row = {"q": x["q"], "gold": x["gold"], "a": x["chosen"], "b": y["chosen"]}
        (both if x["hit"] and y["hit"] else neither if not (x["hit"] or y["hit"])
         else wins if y["hit"] else losses).append(row)
    return {
        "a": a.get("label", "A"), "b": b.get("label", "B"), "n": len(ca),
        "top1_a": a["top1"], "top1_b": b["top1"], "delta": b["top1"] - a["top1"],
        "b_only": wins, "a_only": losses, "both": len(both), "neither": len(neither),
        "sign_test_p": sign_test_p(len(wins), len(losses)),
        "usd_a": _usd(a), "usd_b": _usd(b),
        "p50_a": a["latency_ms"]["p50"], "p50_b": b["latency_ms"]["p50"],
        "p95_a": a["latency_ms"]["p95"], "p95_b": b["latency_ms"]["p95"],
    }


def render(d: dict) -> str:
    A, B = d["a"], d["b"]
    L = [f"compare  {A}  vs  {B}   ({d['n']} cases)",
         f"  top-1     {A} {d['top1_a']:.3f}   {B} {d['top1_b']:.3f}   delta {d['delta']:+.3f}",
         f"  cases     both right {d['both']}   neither {d['neither']}   "
         f"only {B} {len(d['b_only'])}   only {A} {len(d['a_only'])}",
         f"  sign test p={d['sign_test_p']:.3f} over {len(d['b_only']) + len(d['a_only'])} discordant cases"
         + ("  (not distinguishable from noise)" if d["sign_test_p"] > 0.05 else ""),
         f"  USD       {A} ${d['usd_a']:.4f}   {B} ${d['usd_b']:.4f}",
         f"  latency   p50 {d['p50_a']:.0f}/{d['p50_b']:.0f} ms   p95 {d['p95_a']:.0f}/{d['p95_b']:.0f} ms  ({A}/{B})"]
    for title, rows in ((f"only {B} got right (wins)", d["b_only"]), (f"only {A} got right (losses)", d["a_only"])):
        L += ["", title]
        L += [f"  - {r['q']}\n      gold {r['gold']}   {A}->{r['a'] or '-'}   {B}->{r['b'] or '-'}" for r in rows] or ["  (none)"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jiffy.bench.compare")
    ap.add_argument("files", nargs=2, metavar=("A.json", "B.json"), help="two saved `jiffy bench --json` results")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        ra, rb = (json.loads(Path(f).read_text()) for f in a.files)
        d = diff(ra, rb)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(d, indent=1) if a.json else render(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
