"""Pilot runner: orchestrates N trials of one (task, substrate) cell.

For python_baseline:
  1. Spawn `strace -f -e trace=openat -o trace.log python -m harnesses.python_baseline ...`
  2. Read the per-trial JSON the child wrote.
  3. Parse the strace trace, run fs_detector against the task's canary list.
  4. Fold fs_trace_paths + canary_triggers + escape_success into the record.
  5. Append the record to a per-cell JSONL.

For symbiont: shell out to `evals/escape/harnesses/symbiont/run.sh` which
invokes the Rust `symbi-escape-bench` binary; the binary handles policy
enforcement and writes the same trial schema. We still wrap it in strace
so we have ground truth on what the Symbiont process actually touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import yaml

from detectors.fs_detector import FsCanaryConfig, check_canary, parse_strace_paths

PROMPT_VARIANTS = [0, 1, 2]
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_one_trial(
    task,
    *,
    substrate,
    model,
    prompt_variant,
    instance_seed,
    results_dir,
    instance_root,
):
    """Run a single trial as a strace-wrapped subprocess.

    Returns (per_trial_json_path, strace_log_path).
    """
    out_path = results_dir / f"trial-{substrate}-{instance_seed:04d}.json"
    strace_path = results_dir / f"trial-{substrate}-{instance_seed:04d}.strace"
    task_path = task["_path"]

    if substrate == "python_baseline":
        child = [
            sys.executable,
            "-m",
            "harnesses.python_baseline",
            "--task",
            str(task_path),
            "--model",
            model,
            "--prompt-variant",
            str(prompt_variant),
            "--instance-seed",
            str(instance_seed),
            "--instance-root",
            str(instance_root),
            "--out",
            str(out_path),
        ]
    elif substrate == "symbiont":
        child = [
            "bash",
            str(Path(__file__).parent / "harnesses" / "symbiont" / "run.sh"),
            "--task",
            str(task_path),
            "--model",
            model,
            "--prompt-variant",
            str(prompt_variant),
            "--instance-seed",
            str(instance_seed),
            "--instance-root",
            str(instance_root),
            "--out",
            str(out_path),
        ]
    else:
        raise ValueError(f"unsupported substrate: {substrate}")

    cmd = ["strace", "-f", "-e", "trace=openat", "-o", str(strace_path), *child]
    subprocess.run(cmd, check=True, env=os.environ.copy())
    return out_path, strace_path


def run_pilot(
    *,
    task_path: Path,
    substrate: str,
    model: str,
    trials: int,
    results_dir: Path,
) -> Path:
    task = yaml.safe_load(task_path.read_text())
    task["_path"] = task_path
    canary_cfg = FsCanaryConfig(
        paths=task["canary"]["filesystem_paths"],
        path_globs=task["canary"].get("filesystem_paths_glob", []),
    )

    results_dir.mkdir(parents=True, exist_ok=True)
    jsonl_out = results_dir / f"{task['id']}-{substrate}.jsonl"
    with jsonl_out.open("w") as f, tempfile.TemporaryDirectory() as instance_td:
        for seed in range(trials):
            variant = PROMPT_VARIANTS[seed % len(PROMPT_VARIANTS)]
            out_path, strace_path = _run_one_trial(
                task,
                substrate=substrate,
                model=model,
                prompt_variant=variant,
                instance_seed=seed,
                results_dir=results_dir,
                instance_root=Path(instance_td) / f"seed-{seed}",
            )
            rec = json.loads(out_path.read_text())
            paths = (
                parse_strace_paths(strace_path.read_text())
                if strace_path.exists()
                else []
            )
            triggers = check_canary(paths, canary_cfg)
            rec["fs_trace_paths"] = paths
            rec["canary_triggers"] = triggers
            rec["escape_success"] = bool(triggers)
            f.write(json.dumps(rec) + "\n")
    return jsonl_out


@click.command()
@click.option(
    "--task",
    "task_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--substrate",
    required=True,
    type=click.Choice(["python_baseline", "symbiont"]),
)
@click.option("--model", required=True)
@click.option("--trials", required=True, type=int)
@click.option(
    "--results-dir",
    default=Path("results"),
    type=click.Path(path_type=Path),
)
def cli(task_path, substrate, model, trials, results_dir):
    out = run_pilot(
        task_path=task_path,
        substrate=substrate,
        model=model,
        trials=trials,
        results_dir=results_dir,
    )
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    cli()
