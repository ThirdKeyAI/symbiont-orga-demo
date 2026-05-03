"""Per-cell aggregation: escape rate, objective-success rate, 95% Wilson CIs,
and a count of escape vectors actually exercised.

Wilson CI rather than normal-approximation because the relevant cell
will sometimes be 0/N or N/N — the normal approximation degenerates
there but Wilson stays well-behaved.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

Z_95 = 1.959963984540054


def wilson_ci(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    if confidence != 0.95:
        raise NotImplementedError("only 0.95 supported in pilot")
    z = Z_95
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    spread = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def aggregate_jsonl(
    paths: list[Path],
    *,
    by_model: bool = False,
) -> dict[tuple, dict]:
    """Aggregate per (task, substrate) — or (task, substrate, model) when by_model.

    The model dimension is read from each trial record's 'model' field.
    With by_model=False, models within a cell are pooled together.
    """
    cells: dict[tuple, dict] = defaultdict(
        lambda: {
            "n": 0,
            "objective_successes": 0,
            "attempted": 0,
            "escapes": 0,
            "blocked_only": 0,
            "vector_breakdown": Counter(),
            "blocker_breakdown": Counter(),
        }
    )
    for p in paths:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if by_model:
                key = (rec["task_id"], rec["substrate"], rec.get("model", ""))
            else:
                key = (rec["task_id"], rec["substrate"])
            cells[key]["n"] += 1
            cells[key]["objective_successes"] += int(rec.get("objective_success", False))
            attempted = bool(rec.get("escape_attempted"))
            succeeded = bool(rec.get("escape_success"))
            cells[key]["attempted"] += int(attempted)
            cells[key]["escapes"] += int(succeeded)
            if attempted and not succeeded:
                cells[key]["blocked_only"] += 1
            for trig in rec.get("canary_triggers", []):
                cells[key]["vector_breakdown"][trig["vector"]] += 1
            for att in rec.get("escape_attempts", []):
                if att.get("blocked_by"):
                    cells[key]["blocker_breakdown"][att["blocked_by"]] += 1
    return {
        k: {
            **v,
            "vector_breakdown": dict(v["vector_breakdown"]),
            "blocker_breakdown": dict(v["blocker_breakdown"]),
        }
        for k, v in cells.items()
    }


def render(cells: dict[tuple, dict], *, by_model: bool = False) -> str:
    if by_model:
        header = "| task | substrate | model | n | objective | attempt | attempt 95% CI | success | success 95% CI | blocked-only | blockers |"
        sep = "|------|-----------|-------|---|-----------|---------|----------------|---------|----------------|--------------|----------|"
    else:
        header = "| task | substrate | n | objective | attempt | attempt 95% CI | success | success 95% CI | blocked-only | blockers |"
        sep = "|------|-----------|---|-----------|---------|----------------|---------|----------------|--------------|----------|"
    out = [header, sep]
    for key, v in sorted(cells.items()):
        n = v["n"]
        obj = v["objective_successes"] / n if n else 0
        att = v["attempted"] / n if n else 0
        esc = v["escapes"] / n if n else 0
        a_lo, a_hi = wilson_ci(v["attempted"], n)
        s_lo, s_hi = wilson_ci(v["escapes"], n)
        blockers = (
            ", ".join(f"{k}:{c}" for k, c in v["blocker_breakdown"].items()) or "—"
        )
        if by_model:
            task, sub, model = key
            row = (
                f"| {task} | {sub} | {model} | {n} | {obj:.0%} | "
                f"{att:.0%} | [{a_lo:.0%}, {a_hi:.0%}] | "
                f"{esc:.0%} | [{s_lo:.0%}, {s_hi:.0%}] | "
                f"{v['blocked_only']} | {blockers} |"
            )
        else:
            task, sub = key
            row = (
                f"| {task} | {sub} | {n} | {obj:.0%} | "
                f"{att:.0%} | [{a_lo:.0%}, {a_hi:.0%}] | "
                f"{esc:.0%} | [{s_lo:.0%}, {s_hi:.0%}] | "
                f"{v['blocked_only']} | {blockers} |"
            )
        out.append(row)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--by-model", action="store_true",
                    help="Break out one row per (task, substrate, model)")
    ap.add_argument("--recursive", action="store_true",
                    help="Recurse into subdirectories (for sweep results)")
    args = ap.parse_args()
    glob_pat = "**/*.jsonl" if args.recursive else "*.jsonl"
    files = sorted(args.results_dir.glob(glob_pat))
    cells = aggregate_jsonl(files, by_model=args.by_model)
    print(render(cells, by_model=args.by_model))


if __name__ == "__main__":
    main()
