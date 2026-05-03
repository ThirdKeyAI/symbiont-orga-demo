# Escape Evaluation Harness

Parallel evaluation framework that measures whether substrate-level
constraints (Symbiont typestate + Cedar + ToolClad vs permissive Python
tool registries) actually prevent agents from exceeding declared
capabilities. Independent of the Karpathy learning loop in
`crates/demo-karpathy-loop` — this harness reuses the Symbiont policy
infrastructure but does not modify the loop.

## Layout

- `tasks/` — YAML task definitions (objective, declared capabilities, canary list).
- `detectors/` — substrate-independent canary detectors (filesystem in pilot).
- `harnesses/` — substrate adapters (`python_baseline`, `symbiont`).
- `runner.py` — orchestrates N trials × M substrates, writes JSONL.
- `analysis/` — per-cell escape rate + 95% CI.
- `results/` — JSONL trial records (gitignored).
- `tests/` — unit + integration tests.

## Pilot

Single task (`fs_boundary`), two substrates (`python_baseline`, `symbiont`),
20 trials per cell, run locally:

```
cd evals/escape
pip install -e .[dev]
python runner.py --task tasks/fs_boundary.yaml --substrate python_baseline --model deepseek/deepseek-v4-flash --trials 20
python runner.py --task tasks/fs_boundary.yaml --substrate symbiont          --model deepseek/deepseek-v4-flash --trials 20
python -m analysis.aggregate results/
```

See `docs/plans/2026-05-02-escape-evals-pilot.md` for the full plan.
