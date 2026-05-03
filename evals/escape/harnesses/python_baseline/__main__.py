"""CLI entry: `python -m harnesses.python_baseline --task ... --out ...`

Runs exactly one trial. The runner invokes this as a child process under
strace so we get a per-trial fs trace.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from harnesses.python_baseline.harness import run_trial


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, type=Path)
    p.add_argument("--model", required=True)
    p.add_argument("--prompt-variant", required=True, type=int)
    p.add_argument("--instance-seed", required=True, type=int)
    p.add_argument("--instance-root", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    task = yaml.safe_load(args.task.read_text())
    rec = run_trial(
        task,
        model=args.model,
        prompt_variant=args.prompt_variant,
        instance_seed=args.instance_seed,
        instance_root=args.instance_root,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rec.to_dict()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
