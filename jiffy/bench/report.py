"""Human table for one result. `--json` prints the result dict instead."""
from __future__ import annotations


def render(res: dict) -> str:
    L = []
    live = res["mode"] == "live"
    L.append(f"jiffy bench  cases={res['n_cases']}  mode={res['mode'].upper()}"
             f"  judge={','.join(res['judges']) or '-'}")
    if not live:
        L.append("DRY: no paid or network calls; judge outputs are stub noise, so top-1/recall "
                 "below are meaningless — only calls, tokens and USD are estimates.")
    else:
        L.append(f"top-1 accuracy   {res['top1']:.3f}   ({res['top1_hits']}/{res['n_cases']})")
    ln = res.get("line")
    if ln and live:
        L.append(f"line_ok          {ln['ok_rate']:.3f}   ({ln['ok_n']}/{ln['graded']} graded, "
                 f"p>={ln['threshold']})   unfilled {ln['unfilled_rate']:.3f}   grader ${ln['usd']:.4f}")
        if ln["failed"]:
            L.append(f"  !! {ln['failed']} grade(s) failed: excluded, not counted as passes")
        for key in ("by_split", "by_type"):
            for g, b in ln.get(key, {}).items():
                L.append(f"  {g:<14}{b['ok_rate']:.3f}  ({b['ok_n']}/{b['graded']})")
    L.append("")
    L.append("recall by stage — a candidate lost here cannot be recovered by any later judge")
    L.append(f"  {'stage':<12}{'kind':<10}{'M(med)':>7}{'recall':>8}")
    for s in res["stages"]:
        L.append(f"  {s['node']:<12}{s['kind']:<10}{s['m_median']:>7.0f}{s['recall']:>8.3f}")
    L.append(f"  {'top-1':<12}{'':<10}{1:>7}{res['top1']:>8.3f}")
    rr = res["retrieve_recall"]
    L.append("  retrieval order: " + "  ".join(f"recall{k} {v:.3f}" for k, v in rr.items()))
    if not res["recall_monotonic"]:
        L.append("  !! NON-MONOTONIC: recall rose down the stages — a stage added candidates or the harness is wrong")
    L.append("")
    lat = res["latency_ms"]
    L.append(f"latency   p50 {lat['p50']:.0f} ms   p95 {lat['p95']:.0f} ms   mean {lat['mean']:.0f} ms")
    est = " (ESTIMATED)" if res["usd_estimated"] else ""
    L.append(f"USD       total ${res['usd']:.4f}{est}   per case ${res['usd_per_case']:.5f}")
    if "estimate_full_usd" in res:
        L.append(f"          full case set would cost ~${res['estimate_full_usd']:.4f}")
    L.append("")
    L.append(f"{'stage':<12}{'calls':>7}{'degraded':>10}")
    for s in res["stages"]:
        L.append(f"{s['node']:<12}{res['calls_per_node'].get(s['node'], 0):>7}"
                 f"{res['degraded_per_node'].get(s['node'], 0):>10}")
    if res["degraded_total"]:
        L.append(f"!! {res['degraded_total']} stage degradation(s): fallbacks ran, numbers are of a weakened run")
    if res.get("path"):
        L.append(f"\nsaved {res['path']}")
    return "\n".join(L)
