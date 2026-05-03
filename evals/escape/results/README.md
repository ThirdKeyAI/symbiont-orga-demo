# results/ — Local-Only Trial Data

Files in this directory are **deliberately gitignored** and must not be staged, attached to public writeups, or shared without redaction.

## Why

Per-trial JSONLs and strace traces under `results/` contain:

- the literal Python the model used to escape (`tool_calls[].args.code`)
- the exact paths/URLs/syscalls the model targeted (`escape_attempts[].evidence`)
- the model's full reasoning chain (`model_output_trace[].content`)

A per-trial record is a worked exploit recipe specific to this substrate. Aggregating is fine; sharing raw is not.

## Gitignored patterns

```
evals/escape/results/*.jsonl
evals/escape/results/*.json
evals/escape/results/*.strace
evals/escape/results/sweep/
```

`results/.gitkeep` is the only file here that ships.

## If you need to share trial data

Run `python scripts/redact_trials.py <input.jsonl> > public.jsonl` first. The redactor scrubs exploit content while preserving aggregates (token counts, blocker labels, success/failure flags, request IDs).

See `evals/escape/PUBLISHABILITY.md` for the full policy.
