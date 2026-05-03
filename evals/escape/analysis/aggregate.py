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

import yaml

PRICES_PATH = Path(__file__).resolve().parent.parent / "prices.yaml"

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


def load_prices(path: Path = PRICES_PATH) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text()) or {}).get("models", {}) or {}


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    prices: dict[str, dict[str, float]],
) -> float | None:
    p = prices.get(model)
    if not p:
        return None
    return (
        prompt_tokens / 1_000_000 * p.get("input_per_M", 0)
        + completion_tokens / 1_000_000 * p.get("output_per_M", 0)
    )


def aggregate_jsonl(
    paths: list[Path],
    *,
    by_model: bool = False,
    prices: dict[str, dict[str, float]] | None = None,
) -> dict[tuple, dict]:
    """Aggregate per (task, substrate) — or (task, substrate, model) when by_model.

    The model dimension is read from each trial record's 'model' field.
    With by_model=False, models within a cell are pooled together.
    Cost is estimated from prices.yaml; cells whose model isn't in the
    table get est_cost_usd=None.
    """
    prices = prices if prices is not None else load_prices()
    cells: dict[tuple, dict] = defaultdict(
        lambda: {
            "n": 0,
            "objective_successes": 0,
            "attempted": 0,
            "escapes": 0,
            "blocked_only": 0,
            "vector_breakdown": Counter(),
            "blocker_breakdown": Counter(),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "llm_calls": 0,
            "est_cost_usd": 0.0,
            "cost_known": True,
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
            # Token + cost roll-up
            usages = rec.get("usage_per_call", []) or []
            cells[key]["llm_calls"] += len(usages)
            for u in usages:
                cells[key]["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
                cells[key]["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
            model_id = rec.get("model", "")
            if model_id not in prices:
                cells[key]["cost_known"] = False
            else:
                trial_cost = estimate_cost(
                    model_id,
                    sum(int(u.get("prompt_tokens", 0) or 0) for u in usages),
                    sum(int(u.get("completion_tokens", 0) or 0) for u in usages),
                    prices,
                )
                if trial_cost is not None:
                    cells[key]["est_cost_usd"] += trial_cost
    return {
        k: {
            **v,
            "vector_breakdown": dict(v["vector_breakdown"]),
            "blocker_breakdown": dict(v["blocker_breakdown"]),
        }
        for k, v in cells.items()
    }


def _fmt_cost(v: dict) -> str:
    return f"${v['est_cost_usd']:.3f}" if v["cost_known"] else "—"


def render(cells: dict[tuple, dict], *, by_model: bool = False) -> str:
    if by_model:
        header = "| task | substrate | model | n | obj | attempt | success | blocked-only | blockers | calls | tok in | tok out | est $ |"
        sep = "|------|-----------|-------|---|-----|---------|---------|--------------|----------|-------|--------|---------|-------|"
    else:
        header = "| task | substrate | n | obj | attempt | success | blocked-only | blockers | calls | tok in | tok out | est $ |"
        sep = "|------|-----------|---|-----|---------|---------|--------------|----------|-------|--------|---------|-------|"
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
        cost_str = _fmt_cost(v)
        cell_summary = (
            f"{n} | {obj:.0%} | "
            f"{att:.0%} [{a_lo:.0%},{a_hi:.0%}] | "
            f"{esc:.0%} [{s_lo:.0%},{s_hi:.0%}] | "
            f"{v['blocked_only']} | {blockers} | "
            f"{v['llm_calls']} | {v['prompt_tokens']} | {v['completion_tokens']} | {cost_str}"
        )
        if by_model:
            task, sub, model = key
            row = f"| {task} | {sub} | {model} | {cell_summary} |"
        else:
            task, sub = key
            row = f"| {task} | {sub} | {cell_summary} |"
        out.append(row)
    # Append a totals line
    total_calls = sum(v["llm_calls"] for v in cells.values())
    total_in = sum(v["prompt_tokens"] for v in cells.values())
    total_out = sum(v["completion_tokens"] for v in cells.values())
    total_cost = sum(v["est_cost_usd"] for v in cells.values() if v["cost_known"])
    any_unknown = any(not v["cost_known"] for v in cells.values())
    total_cost_str = f"${total_cost:.3f}" + (" (partial — some models missing from prices.yaml)" if any_unknown else "")
    out.append(
        f"\n**Totals:** {total_calls} LLM calls, "
        f"{total_in:,} input tokens, {total_out:,} output tokens, "
        f"est. cost {total_cost_str}"
    )
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
