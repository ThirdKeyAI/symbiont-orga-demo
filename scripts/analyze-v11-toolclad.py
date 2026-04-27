#!/usr/bin/env python3
"""v11 sweep analyzer — produces the per-model A/B matrix.

Reads:
  - data/<tag>-tai/runs.db       (control arm)
  - data/<tag>-tai-tcd/runs.db   (treatment arm)
  - journals-<tag>-tai/*-task-whois-capture.jsonl (control captures)
  - journals-<tag>-tai-tcd/*-task-whois-capture.jsonl (treatment captures)

Emits:
  - demo-output/v11-bite-rate-matrix.md  (markdown table for the report)
  - demo-output/v11-summary.json         (machine-readable headline numbers)

Usage:
  scripts/analyze-v11-toolclad.py [--models gpt-oss-20b,haiku45,...]

Defaults to the standard 9-model OpenRouter sweep group.
"""

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_MODELS = [
    "gpt5", "gemini25pro", "haiku45", "deepseekv31",
    "qwen3-235b", "mimo-v2-pro", "minimax-m27",
    "gpt-oss-20b", "qwen36-plus",
]

SUB_SHAPES = [
    "metachar", "cmd-subst", "backtick", "wildcard",
    "newline", "traversal", "homoglyph-idn", "punycode-idn",
]


def load_capture(journals_dir: Path) -> list[dict]:
    """Concatenate every *-task-whois-capture.jsonl file in `journals_dir`."""
    rows = []
    if not journals_dir.exists():
        return rows
    for path in sorted(journals_dir.glob("*-task-whois-capture.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('{"_meta'):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"warn: bad jsonl in {path}: {e}", file=sys.stderr)
    return rows


def total_cost(db_path: Path) -> float:
    if not db_path.exists():
        return 0.0
    conn = sqlite3.connect(str(db_path))
    try:
        (cost,) = conn.execute(
            "SELECT COALESCE(SUM(est_cost), 0) FROM runs"
        ).fetchone()
        return float(cost or 0)
    finally:
        conn.close()


def per_subshape_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Returns {sub_shape: {outcome: count}} for the captured rows."""
    out = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        ss = r.get("sub_shape") or "(unrecognised)"
        out[ss][r.get("outcome", "(unknown)")] += 1
    return out


def render_per_model_table(matrix: list[dict]) -> str:
    """matrix items: {model, control_attempts, treatment_attempts,
    treatment_refused, control_cost, treatment_cost}."""
    lines = [
        "| model | control attempts | treatment attempts | treatment refused | bite-rate | control $ | treatment $ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    tot = collections.Counter()
    for row in matrix:
        bite = (
            f"{100.0 * row['treatment_refused'] / row['treatment_attempts']:.1f}%"
            if row["treatment_attempts"] > 0 else "n/a"
        )
        lines.append(
            f"| {row['model']} | {row['control_attempts']} | "
            f"{row['treatment_attempts']} | {row['treatment_refused']} | "
            f"{bite} | {row['control_cost']:.4f} | {row['treatment_cost']:.4f} |"
        )
        tot["control_attempts"] += row["control_attempts"]
        tot["treatment_attempts"] += row["treatment_attempts"]
        tot["treatment_refused"] += row["treatment_refused"]
        tot["control_cost"] += row["control_cost"]
        tot["treatment_cost"] += row["treatment_cost"]
    bite_total = (
        f"{100.0 * tot['treatment_refused'] / tot['treatment_attempts']:.1f}%"
        if tot["treatment_attempts"] > 0 else "n/a"
    )
    lines.append(
        f"| **TOTAL** | **{tot['control_attempts']}** | "
        f"**{tot['treatment_attempts']}** | **{tot['treatment_refused']}** | "
        f"**{bite_total}** | **{tot['control_cost']:.4f}** | "
        f"**{tot['treatment_cost']:.4f}** |"
    )
    return "\n".join(lines)


def render_subshape_table(per_arm: dict[str, dict]) -> str:
    """per_arm: {arm: {sub_shape: Counter(outcome -> count)}}."""
    lines = [
        "| sub-shape | control attempts | treatment attempts | treatment refused | bite-rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for ss in SUB_SHAPES + ["(unrecognised)"]:
        c = per_arm.get("control", {}).get(ss, collections.Counter())
        t = per_arm.get("treatment", {}).get(ss, collections.Counter())
        ca = sum(c.values())
        ta = sum(t.values())
        tr = t.get("refused", 0)
        bite = f"{100.0 * tr / ta:.1f}%" if ta > 0 else "n/a"
        lines.append(f"| {ss} | {ca} | {ta} | {tr} | {bite} |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    p.add_argument("--root", default=".",
                   help="repo root (defaults to current dir)")
    p.add_argument(
        "--out-md", default="demo-output/v11-bite-rate-matrix.md")
    p.add_argument("--out-json", default="demo-output/v11-summary.json")
    args = p.parse_args()

    root = Path(args.root)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    matrix = []
    per_arm = {"control": collections.defaultdict(collections.Counter),
               "treatment": collections.defaultdict(collections.Counter)}

    for model in models:
        ctrl_journals = root / f"journals-{model}-tai"
        treat_journals = root / f"journals-{model}-tai-tcd"
        ctrl_db = root / f"data/{model}-tai/runs.db"
        treat_db = root / f"data/{model}-tai-tcd/runs.db"

        ctrl_rows = load_capture(ctrl_journals)
        treat_rows = load_capture(treat_journals)

        treat_refused = sum(1 for r in treat_rows
                            if r.get("outcome") == "refused")

        for ss, counter in per_subshape_counts(ctrl_rows).items():
            per_arm["control"][ss].update(counter)
        for ss, counter in per_subshape_counts(treat_rows).items():
            per_arm["treatment"][ss].update(counter)

        matrix.append({
            "model": model,
            "control_attempts": len(ctrl_rows),
            "treatment_attempts": len(treat_rows),
            "treatment_refused": treat_refused,
            "control_cost": total_cost(ctrl_db),
            "treatment_cost": total_cost(treat_db),
        })

    # Summary numbers for the README headline row.
    total_treatment_attempts = sum(m["treatment_attempts"] for m in matrix)
    total_treatment_refused = sum(m["treatment_refused"] for m in matrix)
    headline = (
        f"{total_treatment_refused} / {total_treatment_attempts}"
        if total_treatment_attempts > 0
        else "n/a (treatment arm produced no calls)"
    )

    md_blocks = [
        "# v11 ToolClad fence — A/B bite-rate matrix",
        "",
        f"**Headline:** **{headline}** typed-argument-fence refusals "
        f"across {len(models)} model(s). Generated by "
        f"`scripts/analyze-v11-toolclad.py`.",
        "",
        "## Per-model A/B",
        "",
        render_per_model_table(matrix),
        "",
        "## Per-sub-shape bite-rate",
        "",
        render_subshape_table(per_arm),
        "",
    ]

    out_md = root / args.out_md
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md_blocks))

    summary = {
        "headline": headline,
        "treatment_attempts_total": total_treatment_attempts,
        "treatment_refused_total": total_treatment_refused,
        "control_attempts_total": sum(m["control_attempts"] for m in matrix),
        "models": matrix,
        "per_arm_sub_shape": {
            arm: {ss: dict(counter) for ss, counter in d.items()}
            for arm, d in per_arm.items()
        },
    }
    out_json = root / args.out_json
    out_json.write_text(json.dumps(summary, indent=2))
    print(f"wrote {out_md}")
    print(f"wrote {out_json}")
    print(f"\nheadline: {headline}")


if __name__ == "__main__":
    main()
