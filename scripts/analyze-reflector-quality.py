#!/usr/bin/env python3
"""Post-hoc reflector-quality analysis.

For each (model, task), compare iteration N=2 and N=3 against the cold
start (N=1) to decide whether the reflector's stored procedures made
the task agent faster or more reliable. We also aggregate the
OpenRouter per-call JSONL sidecars (when present) to show authoritative
cost / upstream provider.

Run:
    scripts/analyze-reflector-quality.py            # current DBs under data/
    scripts/analyze-reflector-quality.py --suffix -adv  # adversarial sweep

Outputs:
    demo-output/reflector-quality.json
    demo-output/reflector-quality.txt  (human-readable table)

The script makes one simple call: given n1_iter, n2_iter, n3_iter, did
the iteration count go down? Same for total_tokens. Flag each
(model, task) as `improved`, `flat`, or `regressed`. Surface
aggregates: how often does the reflector actually help each model?
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TaskCurve:
    model_tag: str
    task_id: str
    scores: list
    iters: list
    tokens: list
    terms: list
    verdict: str  # improved / flat / regressed / incomplete
    # Positive = reflector helped (iterations dropped)
    iter_delta_pct: float
    token_delta_pct: float
    # Per-task authoritative cost (sum over all runs) if sidecars are
    # present for this (tag, task), else None.
    auth_cost_usd: float | None


def load_curve(db_path: Path, tag: str) -> list[TaskCurve]:
    c = sqlite3.connect(db_path)
    tasks = [r[0] for r in c.execute(
        "SELECT DISTINCT task_id FROM runs WHERE kind='task' ORDER BY task_id"
    )]
    out = []
    for t in tasks:
        rows = list(c.execute(
            """SELECT run_number, score, iterations, total_tokens, termination_reason
               FROM runs WHERE kind='task' AND task_id=?
               ORDER BY run_number""",
            (t,),
        ))
        if len(rows) < 2:
            continue
        scores = [r[1] for r in rows]
        iters = [r[2] for r in rows]
        tokens = [r[3] for r in rows]
        terms = [r[4] for r in rows]

        n1_iter = iters[0] or 1
        n_after = iters[-1]
        iter_delta_pct = 100.0 * (n1_iter - n_after) / n1_iter if n1_iter else 0.0
        n1_tok = tokens[0] or 1
        tok_delta_pct = 100.0 * (n1_tok - tokens[-1]) / n1_tok if n1_tok else 0.0

        # Verdict rules:
        #  - `incomplete` if any run timed out / erred.
        #  - `improved` if later iterations had ≥15% fewer iterations
        #    OR a score lifted from <0.5 to ≥0.5.
        #  - `regressed` if iterations grew ≥15% or score dropped below 0.5.
        #  - otherwise `flat`.
        any_bad_term = any(
            tr and (("timeout" in tr) or ("error" in tr.lower()))
            for tr in terms
        )
        recovered = scores[0] < 0.5 and any(s >= 0.5 for s in scores[1:])
        score_regressed = scores[0] >= 0.5 and scores[-1] < 0.5
        if any_bad_term:
            verdict = "incomplete"
        elif recovered:
            verdict = "improved"
        elif score_regressed:
            verdict = "regressed"
        elif iter_delta_pct >= 15.0:
            verdict = "improved"
        elif iter_delta_pct <= -15.0:
            verdict = "regressed"
        else:
            verdict = "flat"

        out.append(TaskCurve(
            model_tag=tag,
            task_id=t,
            scores=scores,
            iters=iters,
            tokens=tokens,
            terms=terms,
            verdict=verdict,
            iter_delta_pct=round(iter_delta_pct, 1),
            token_delta_pct=round(tok_delta_pct, 1),
            auth_cost_usd=None,
        ))
    return out


def load_auth_cost(journals_dir: Path) -> dict[str, float]:
    """Sum OpenRouter authoritative cost per task_id from JSONL sidecars."""
    out: dict[str, float] = {}
    for p in journals_dir.glob("*-calls.jsonl"):
        # Filename: YYYYMMDD-HHMMSS-Tx-nNNN-{task,reflect}-calls.jsonl
        parts = p.stem.split("-")
        # tasks are in position 2 relative to stem; be permissive.
        task = next((x for x in parts if x.startswith("T") and len(x) <= 3), None)
        if task is None:
            continue
        try:
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    out[task] = out.get(task, 0.0) + float(d.get("cost_usd") or 0.0)
        except Exception:
            continue
    return out


def model_tags(suffix: str) -> list[str]:
    # Any dir under data/ whose name ends with the chosen suffix (or no
    # suffix, for default runs). Sorted for stable output.
    tags = []
    for p in sorted(Path(ROOT, "data").iterdir()):
        if not p.is_dir():
            continue
        if suffix:
            if p.name.endswith(suffix):
                tags.append(p.name)
        else:
            if not p.name.endswith("-adv") and (ROOT / "data" / p.name / "runs.db").exists():
                tags.append(p.name)
    return tags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="", help='Dir suffix (e.g. "-adv")')
    ap.add_argument("--out", default="demo-output/reflector-quality")
    args = ap.parse_args()

    tags = model_tags(args.suffix)
    if not tags:
        print(f"no data/<tag>{args.suffix} dirs found")
        return 1

    all_curves: list[TaskCurve] = []
    for tag in tags:
        db = ROOT / "data" / tag / "runs.db"
        if not db.exists():
            continue
        journals = ROOT / f"journals-{tag}"
        auth = load_auth_cost(journals) if journals.exists() else {}
        curves = load_curve(db, tag)
        for cv in curves:
            cv.auth_cost_usd = auth.get(cv.task_id)
        all_curves.extend(curves)

    # Aggregate per-model verdicts.
    per_model: dict[str, dict[str, int]] = {}
    for cv in all_curves:
        s = per_model.setdefault(cv.model_tag, {"improved": 0, "flat": 0,
                                                 "regressed": 0, "incomplete": 0})
        s[cv.verdict] += 1

    # Write JSON.
    jpath = Path(ROOT, f"{args.out}{args.suffix}.json")
    jpath.parent.mkdir(parents=True, exist_ok=True)
    with open(jpath, "w") as f:
        json.dump({
            "per_task": [asdict(c) for c in all_curves],
            "per_model": per_model,
        }, f, indent=2)

    # Write text table.
    tpath = Path(ROOT, f"{args.out}{args.suffix}.txt")
    with open(tpath, "w") as f:
        f.write(f"Reflector-quality analysis ({args.suffix or 'default'})\n")
        f.write("=" * 60 + "\n\n")
        f.write("Per-model verdict counts (task ⇒ verdict):\n\n")
        f.write(f"  {'model':<22} imp  flat reg  incpl\n")
        for tag in sorted(per_model):
            v = per_model[tag]
            f.write(f"  {tag:<22} {v['improved']:<4} {v['flat']:<4} "
                    f"{v['regressed']:<4} {v['incomplete']}\n")

        f.write("\nPer-task detail:\n\n")
        f.write(f"  {'model':<22} {'task':<4} {'n=1..3 iters':<14} "
                f"Δiter%  Δtok%  verdict\n")
        for cv in all_curves:
            iters_str = "/".join(str(x) for x in cv.iters)
            f.write(f"  {cv.model_tag:<22} {cv.task_id:<4} "
                    f"{iters_str:<14} {cv.iter_delta_pct:>+6.1f}  "
                    f"{cv.token_delta_pct:>+6.1f}  {cv.verdict}\n")

    print(f"wrote {jpath}")
    print(f"wrote {tpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
