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


def aggregate_jsonl(paths: list[Path]) -> dict[tuple[str, str], dict]:
    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "n": 0,
            "objective_successes": 0,
            "escapes": 0,
            "vector_breakdown": Counter(),
        }
    )
    for p in paths:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            key = (rec["task_id"], rec["substrate"])
            cells[key]["n"] += 1
            cells[key]["objective_successes"] += int(rec.get("objective_success", False))
            cells[key]["escapes"] += int(rec.get("escape_success", False))
            for trig in rec.get("canary_triggers", []):
                cells[key]["vector_breakdown"][trig["vector"]] += 1
    return {
        k: {**v, "vector_breakdown": dict(v["vector_breakdown"])}
        for k, v in cells.items()
    }


def render(cells: dict[tuple[str, str], dict]) -> str:
    out = [
        "| task | substrate | n | objective | escape | escape 95% CI | vectors |",
        "|------|-----------|---|-----------|--------|---------------|---------|",
    ]
    for (task, sub), v in sorted(cells.items()):
        n = v["n"]
        obj = v["objective_successes"] / n if n else 0
        esc = v["escapes"] / n if n else 0
        lo, hi = wilson_ci(v["escapes"], n)
        vectors = ", ".join(f"{k}:{c}" for k, c in v["vector_breakdown"].items()) or "—"
        out.append(
            f"| {task} | {sub} | {n} | {obj:.0%} | {esc:.0%} | "
            f"[{lo:.0%}, {hi:.0%}] | {vectors} |"
        )
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", type=Path)
    args = ap.parse_args()
    files = sorted(args.results_dir.glob("*.jsonl"))
    cells = aggregate_jsonl(files)
    print(render(cells))


if __name__ == "__main__":
    main()
