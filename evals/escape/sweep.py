"""Multi-model sweep orchestrator.

Reads sweep_config.yaml; enumerates (task, substrate, model) cells;
either prints them (default --dry-run) or executes each cell via
runner.run_pilot. Writes one JSONL per cell into results/sweep/.

Naming: results/sweep/<task_id>-<substrate>-<model_slug>.jsonl
where model_slug = the model id with /, :, . replaced by _.

Estimated cost: see `print_estimate()`. With the default config (6
tasks × 2 substrates × 6 models × 20 trials = 1440 trials, ~8 turns
each, ~2KB tokens per turn) you're looking at roughly 23M tokens of
LLM traffic. At deepseek-v4-flash $0.14/M that's ~$3.30 for the
floor model alone; the frontier tiers raise that. Run a single tier
at a time to manage spend.

Usage:
  python sweep.py                       # dry-run, prints planned cells
  python sweep.py --execute             # runs everything
  python sweep.py --execute --tier t2_mid    # only the mid tier
  python sweep.py --execute --task fs_enumerate  # filter by task
  python sweep.py --execute --model deepseek/deepseek-v4-flash
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import click
import yaml

from runner import run_pilot

CONFIG_DEFAULT = Path(__file__).parent / "sweep_config.yaml"


@dataclass(frozen=True)
class Cell:
    task_path: Path
    substrate: str
    model: str
    tier: str

    def slug(self) -> str:
        m = re.sub(r"[/:\.]", "_", self.model)
        task_id = Path(self.task_path).stem
        return f"{task_id}-{self.substrate}-{m}"


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def enumerate_cells(cfg: dict, tier_filter: str | None,
                    task_filter: str | None,
                    substrate_filter: str | None,
                    model_filter: str | None) -> list[Cell]:
    """Cartesian product (task × substrate × model) with optional filters."""
    out: list[Cell] = []
    for model_entry in cfg["models"]:
        if tier_filter and model_entry["tier"] != tier_filter:
            continue
        if model_filter and model_entry["id"] != model_filter:
            continue
        for task_path in cfg["tasks"]:
            if task_filter and task_filter not in task_path:
                continue
            for substrate in cfg["substrates"]:
                if substrate_filter and substrate != substrate_filter:
                    continue
                out.append(Cell(
                    task_path=Path(task_path),
                    substrate=substrate,
                    model=model_entry["id"],
                    tier=model_entry["tier"],
                ))
    return out


def print_estimate(cells: list[Cell], trials_per_cell: int) -> None:
    n = len(cells)
    total_trials = n * trials_per_cell
    # ~8 turns per trial, ~2KB tokens per turn (rough OpenRouter heuristic)
    token_estimate_M = total_trials * 8 * 2 / 1000
    click.echo(f"  cells: {n}")
    click.echo(f"  trials: {total_trials}")
    click.echo(f"  rough token estimate: ~{token_estimate_M:.1f}M (assumes 8 turns × 2KB/turn)")


def run_cell(cell: Cell, trials: int, results_dir: Path) -> Path:
    cell_results_dir = results_dir / cell.slug()
    cell_results_dir.mkdir(parents=True, exist_ok=True)
    return run_pilot(
        task_path=cell.task_path,
        substrate=cell.substrate,
        model=cell.model,
        trials=trials,
        results_dir=cell_results_dir,
    )


@click.command()
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=CONFIG_DEFAULT)
@click.option("--execute", is_flag=True, help="Actually run the sweep (default: dry-run only).")
@click.option("--tier", type=str, default=None, help="Filter by model tier (t1_frontier, t2_mid, t3_floor).")
@click.option("--task", type=str, default=None, help="Substring filter on task path.")
@click.option("--substrate", type=str, default=None, help="Filter to one substrate.")
@click.option("--model", type=str, default=None, help="Filter to one model id.")
def cli(config, execute, tier, task, substrate, model):
    cfg = load_config(config)
    cells = enumerate_cells(cfg, tier, task, substrate, model)
    results_dir = Path(cfg["results_dir"])
    trials = cfg["trials_per_cell"]

    click.echo(f"Sweep config: {config}")
    print_estimate(cells, trials)
    click.echo("")
    for c in cells:
        click.echo(f"  [{c.tier}] {c.task_path.stem} × {c.substrate} × {c.model}")

    if not execute:
        click.echo("\nDry run only. Add --execute to run.")
        return

    click.echo(f"\nExecuting {len(cells)} cells × {trials} trials each → {results_dir}/")
    failures: list[tuple[Cell, str]] = []
    for i, c in enumerate(cells, 1):
        # Skip cells whose JSONL already exists with the right number of lines.
        # Lets us resume after crashes without re-paying for completed work.
        cell_jsonl = results_dir / c.slug() / f"{c.task_path.stem}-{c.substrate}.jsonl"
        if cell_jsonl.exists():
            with open(cell_jsonl) as f:
                done = sum(1 for _ in f)
            if done >= trials:
                click.echo(f"  [{i}/{len(cells)}] {c.slug()} (already complete, skipping)")
                continue
        click.echo(f"  [{i}/{len(cells)}] {c.slug()}")
        try:
            run_cell(c, trials, results_dir)
        except Exception as e:
            click.echo(f"    FAILED: {e!s}")
            failures.append((c, str(e)))
    click.echo(f"\nDone. {len(failures)} cell(s) failed.")
    for c, err in failures:
        click.echo(f"  {c.slug()}: {err}")
    click.echo(f"\nAggregate with: python -m analysis.aggregate {results_dir} --recursive --by-model")


if __name__ == "__main__":
    cli()
