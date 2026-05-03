# Publishability Policy — Escape Evaluation Harness

This subtree generates two classes of artefact. They have different
publishability profiles. **Read this before exporting anything from
`evals/escape/results/` or attaching trial data to a writeup.**

## What is safe to publish

- Task definitions in `tasks/*.yaml` — these describe the *shape* of the test (what's allowed, what fires the canary), not exploit recipes.
- Detector + harness + runner + sweep code in `detectors/`, `harnesses/`, `runner.py`, `sweep.py`, `analysis/`. Reproducibility requires this; nothing here is exploit content.
- Cedar policies / ToolClad manifests / `.symbi` agent files in `harnesses/symbiont/` — substrate configuration, not attacker know-how.
- Aggregate reports in `reports/` (`tier1.md`, `tier1-v2.md`, etc.) — substrate-comparison tables, escape-rate confidence intervals, blocker counts. The credibility of the work depends on these being public.
- Methodology documentation in this file and `README.md`.

## What is NOT safe to publish without redaction

The following are **deliberately gitignored** and must not be staged or attached to public writeups in raw form:

- `results/**/*.jsonl` and `results/**/*.json` — per-trial records.
- `results/**/*.strace` — raw kernel-level traces.
- `results/sweep/**` — entire sweep tree.

The reason: a single per-trial record contains:

- `model_output_trace[].content` — the model's full reasoning chain en route to the escape, including any prompts that worked.
- `tool_calls[].args.code` — when the trial used `python_eval`, the literal Python that performed the escape (`open(policy_path, 'w')`, `subprocess.run(['openssl', ...])`, `urllib.request.urlopen(off_allowlist_url)`, etc.).
- `escape_attempts[].evidence` — exact paths, URLs, or syscall arguments that the model targeted.

Aggregated statistics over thousands of trials don't help an attacker. Concrete worked exploits scraped from these JSONLs do — they're a substrate-specific cookbook.

This is the same convention that CVE-style disclosures use: publish the bug class and the fix; don't publish the working PoC against unpatched targets.

## Redaction workflow

`scripts/redact_trials.py` produces a publishable JSONL from a raw one. Specifically it:

- replaces every `tool_calls[].args.code`, `tool_calls[].args.content`, `tool_calls[].args.path`, `tool_calls[].args.url`, and `tool_calls[].result_summary` with `[REDACTED-EXPLOIT-CONTENT]`
- replaces every `model_output_trace[].content` and `escape_attempts[].evidence` similarly
- preserves `trial_id`, `task_id`, `substrate`, `model`, `prompt_variant`, `instance_seed`, timestamps, `usage_per_call` (token counts + request IDs), `tool_calls[].name`, `tool_calls[].blocked`, `tool_calls[].blocked_by`, `objective_success`, `escape_attempted`, `escape_success`, the structural shape of `escape_attempts` and `canary_triggers` (vector + blocked_by but not evidence)

Run before publishing any per-trial data:

```
python scripts/redact_trials.py results/sweep/<cell>/x.jsonl > public-x.jsonl
```

The redaction is one-way; the raw file stays local.

## What about the existing Karpathy-loop journals?

`demo-output/cloud-t4-20iter-20260418-223014/journals/*.json` are tracked — they were a specific publication choice for the v8 demo (the model is **not** attempting to escape in those trials; they're learning-loop transcripts of a benign task). If you re-publish journals from a new sweep, follow the same threat-model check first: was the model attempting to subvert the substrate, or completing a sanctioned task? Only the latter is publishable raw.

## Crawl-policy signals on public reports

The committed `reports/*.md` files start with HTML-comment AI-training-opt-out markers (`noai`, `noimageai`). Compliance varies by crawler; this is the polite signal that exists. The harder signal is "the data isn't here in the first place" — see the gitignore above.

## When in doubt

Don't push. Ask. The asymmetry is severe: a leaked trial JSONL can be revoked from one site but not from a model that already trained on it.
