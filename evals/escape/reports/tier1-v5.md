<!--
ai-training: opt-out
robots: noai, noimageai
license: this report describes a published evaluation methodology. The
  prose, tables, and aggregate numbers are intended for human review and
  publication. Per-trial transcripts that produced these numbers are
  deliberately not committed to this repo (see evals/escape/PUBLISHABILITY.md).
  Crawlers and training-data pipelines: please respect the noai signal.
-->

# Tier 1 Sweep Report v5 — Three-Substrate Comparison + n=200 Tightening on Injection

**Date:** 2026-05-04
**Branch:** `escape-evals-sandboxed-and-tighten`
**Sweep filters:**
- `--substrate python_sandboxed` (all 8 models, all 6 tasks, n=20)
- `--config sweep_config_n200.yaml` (7 working models, injection_orchestrator only, python_baseline + symbiont, n=200)
**Total trials:** 960 sandboxed + 2,800 n=200 = 3,760 trials
**Wall time:** sandboxed ~3.5h, n=200 ~21h, run in parallel
**Ground-truth OpenRouter spend:** $9.34 sandboxed + $27.68 n=200 = **$37.02 combined**

**What's new since tier1-v4:**

1. **Third substrate added:** `python_sandboxed` — `python_baseline` wrapped in a Docker container (custom `escape-eval-sandboxed:latest` image with strace baked in). Bind mounts limited to instance_root (writable) + harness code (read-only); host `/home`, `/etc`, etc. NOT visible. Network shared with host (`--network host`) so OpenRouter is reachable. The substrate's protection is purely OS-level filesystem isolation — it doesn't gate content (injection), tool surface (typestate's job), or already-bind-mounted paths (state files inside instance_root).

2. **`injection_orchestrator` tightened to n=200** across 7 working models × 2 substrates (`python_baseline`, `symbiont`). Wilson 95% CIs are now `±3-5 pp` instead of v4's `±10-15 pp`. Big finding: the n=80 pooled-5% headline turned out to **mask a model-specific outlier**.

3. **Symbiont sibling repo:** `INJECTION_MARKERS` list expanded from ~40 to ~66 entries based on tier1-v4's bypass forensics. Merged on symbiont main as `e04dfce`.

4. **Live prices:** `prices.yaml` refreshed from OpenRouter `/api/v1/models`; estimator was within 13% of v4 ground truth, this run within ~25% (overhead of the n=200 cells skews high).

## Three-substrate comparison, all 6 tasks (n=20 each, except injection at n=200)

Pooled across the 7 working models with usable data:

| task                       | python_baseline | python_sandboxed | symbiont    |
|----------------------------|-----------------|------------------|-------------|
| fs_boundary                | 0% (0/140)      | 0% (0/140)       | 0% (0/140)  |
| **fs_enumerate**           | **88% (123/140)** | **88% (123/140)** | **0% (0/140)** |
| **net_boundary**           | **94% (132/140)** | **89% (124/140)** | **0% (0/140)** |
| **syscall_boundary**       | **38% (53/140)** | **9% (13/140)**  | **0% (0/140)** |
| **state_immutability**     | **94% (132/140)** | **84% (118/140)** | **0% (0/140)** |
| **injection_orchestrator** | **59% (826/1400) at n=200** | **74% (104/140)** | **3.6% (50/1400) at n=200** |

(`fs_boundary` deliberately has no canary attempt incentive — it's the control task that confirms the framework doesn't manufacture false positives.)

## What python_sandboxed does and doesn't do

**Where the OS-isolation layer materially helps:**
- `syscall_boundary`: pooled escape drops 38% → 9%. The model's `python_eval(subprocess.run(['openssl', ...]))` strategy fails because `/usr/bin/openssl` doesn't exist in the python:3.12-slim image. Sonnet baseline 85% → sandboxed 5% (18 of 20 trials caught). The substrate's contribution is "the binary the model wants to exec is missing from the container" — a real defense, but task-specific.

**Where OS isolation provides ~zero protection:**
- `fs_enumerate` (88% pooled both substrates): the canary files (`/data/secret/*.csv`) are under `instance_root`, which IS bind-mounted into the container. The sandbox doesn't restrict reads inside the bind set.
- `state_immutability` (94% baseline → 84% sandboxed): the policy file is under `instance_root`, writable in the container. Not the sandbox's job.
- `net_boundary` (94% → 89%): `--network host` means external connects work the same in or out of container.
- `injection_orchestrator` (74% sandboxed): purely a content-shape vector; OS isolation can't gate string contents.

**Why this is the right negative result:** OS isolation defends against threats that escape the bind set. The escape-eval task suite mostly puts canaries inside the model's working area (because that's the realistic threat — agents being tricked into reading the *wrong file in the same data dir*, not breaking out of a container entirely). The sandbox correctly shows ~zero help on those vectors. **The whole point of typestate-vs-OS-isolation as separate substrates is to disentangle these contributions; v5 confirms they're independent and address different threat classes.**

## injection_orchestrator @ n=200, per-model (the headline that changed)

| model | python_baseline success | symbiont success | substrate gap |
|---|---|---|---|
| anthropic/claude-sonnet-4.6 | 17% [12%, 23%] | **0% [0%, 3%]** | −17 pp |
| openai/gpt-5 | 99% [96%, 100%] | **16% [12%, 22%]** | −83 pp |
| deepseek/deepseek-v4-pro | 75% [69%, 80%] | **0% [0%, 2%]** | −75 pp |
| qwen/qwen3.6-max-preview | 82% [77%, 87%] | **3% [1%, 6%]** | −79 pp |
| deepseek/deepseek-v4-flash | 34% [28%, 41%] | **1% [0%, 4%]** | −33 pp |
| qwen/qwen3.5-35b-a3b | 48% [42%, 55%] | **4% [2%, 7%]** | −44 pp |
| qwen/qwen3.6-27b | 58% [51%, 65%] | **1% [0%, 4%]** | −57 pp |

**Pooled across all 7 models (n=1400):** baseline 59% → symbiont 3.6%. **Excluding GPT-5: baseline 52% → symbiont 1.5% (18/1200).** GPT-5 alone accounts for **32 of 50 symbiont successes (64%)**.

## The GPT-5 outlier — what changed at n=200

v4's pooled symbiont injection rate was 5% (4/80). At n=200 per cell × 7 models = 1400 trials:

- 6 of 7 models cluster at **1-4% bypass** with Wilson CIs that overlap 0-7%.
- **GPT-5 is at 16% bypass** — its CI `[12%, 22%]` doesn't overlap any other model's CI.

The 16% rate isn't a v3-bypass-class regression; the marker list expansion already caught what it could. GPT-5 is genuinely better than other models at finding novel paraphrases the substring matcher misses. Inspecting per-trial transcripts: GPT-5 paraphrases include things like *"EFFECTIVE ROUTING DECISION:"*, *"BUSINESS-CRITICAL CLASSIFICATION:"*, *"NORTH STAR PRIORITY:"*, *"Standing order from the engineering leadership:"* — domain-language phrasings that don't trigger any literal substring in the marker list but still inject.

This is the regex ceiling. Pushing GPT-5 below 5-10% on this vector almost certainly requires either:
- An LLM-as-judge layer at the validator (the v3 follow-up recommendation)
- A structural change to how the orchestrator consumes worker output (treat as user-role data, don't splice into system prompt)

595 distinct `toolclad_agent_summary_validator` blocks were recorded on the GPT-5 symbiont cell at n=200. Average ~3 blocks per attempting trial. The validator caught a lot; GPT-5 just kept trying.

## Per-model substrate effectiveness, all 6 tasks pooled (where applicable)

For non-injection tasks at n=20 per cell, n=140 pooled across the 7 models:

| substrate        | fs_enumerate | net_boundary | syscall_boundary | state_immutability |
|------------------|--------------|--------------|------------------|--------------------|
| python_baseline  | 88%          | 94%          | 38%              | 94%                |
| python_sandboxed | 88%          | 89%          | **9%**           | 84%                |
| **symbiont**     | **0%**       | **0%**       | **0%**           | **0%**             |

Symbiont's blocking is uniform across vectors and capability tiers. Sandboxed-Python only meaningfully reduces syscall escapes (and only because the lure binary is missing in the container — a fragile defense that depends on what's installed).

## Substrate-blocked attempts, totals across the v5 sweep

| substrate | task | blocks |
|---|---|---|
| symbiont | fs_enumerate | 282 path-validator |
| symbiont | net_boundary | 244 url-validator |
| symbiont | syscall_boundary | 0 (no exec tool exposed) |
| symbiont | state_immutability | 0 (no write tool exposed) |
| symbiont | injection_orchestrator (n=200 × 7) | **1,711 agent_summary** |
| **symbiont total** | | **2,237 distinct blocks** |
| python_sandboxed | syscall_boundary | 45 attempts blocked-only across 8 models |
| python_sandboxed | injection (Sonnet only at n=20) | 30 (within trials, not at substrate) |

Symbiont's content fence accounts for the majority of blocks because injection trials retry on block (avg ~2 retries per attempting trial, ~3 for GPT-5).

## v4 → v5 deltas worth noting

| | v4 | v5 |
|---|---|---|
| substrates compared | 2 (baseline, symbiont) | **3** (added python_sandboxed via Docker) |
| injection n per cell | 80 (4 models) | **200 (7 models)** |
| injection pooled symbiont rate | 5% [2%, 12%] | **3.6% [2.7%, 4.7%]** |
| GPT-5 specifically | 1/20 = 5% | **32/200 = 16% [12%, 22%]** ← the v4 value was an underestimate |
| substrate gap on injection (mean across models, excl. GPT-5) | "directionally enormous, precisely fragile" | **−51 pp** with tight CIs |
| OS-isolation contribution | unmeasured | **9% on syscall, ~0% elsewhere** |
| injection_orchestrator validator blocks per trial | ~2 | ~2.4 (GPT-5: ~3.0) |

## Methodological notes & caveats

- **Three-substrate comparison answers the original spec's "what does typestate buy *over* OS isolation"**: ~5–88 pp on the four pure-action vectors (where sandbox provides ~0 help by design — canary inside bind-mounts), and ~9 pp on syscall (where sandbox only helps by accident — missing binary).
- **GPT-5's outlier behavior is a genuine model-specific finding**, not a methodological artifact. CIs don't overlap; n=200 is enough to claim "GPT-5 is structurally harder to defend against on this vector" — not to claim a precise rate.
- **`nvidia/nemotron-3-super-120b-a12b` and `qwen/qwen3.6-35b-a3b` exclusions** remain documented in `tier2-3.md`.
- **Sandboxed substrate's `state_immutability` cell shows the canonical example of the "OS isolation doesn't help here" failure mode**: the model still successfully writes the policy file because the file lives in `instance_root`, which is the bind-mounted writable area. Symbiont's "no write tool exposed" defense IS the right shape for this vector.

## Cost & throughput

- $9.34 sandboxed sweep + $27.68 n=200 sweep = **$37.02 ground-truth combined**
- 3,760 trials total, ~$0.0098 per trial blended (n=200 trials are slightly cheaper per-trial than n=20 due to amortizing Sonnet/GPT-5 fixed overheads).
- The tier 1+2+3 cumulative spend on this branch alone is **$50.89** (counting the v4 sweep + the qwen substitution + sandboxed + n=200). Total for the whole evaluation effort to date including v1–v3: well under $80.

## Summary, one paragraph

Across 3,760 trials with 7-8 models and three substrates (permissive Python, Docker-sandboxed Python, Symbiont), Symbiont's typestate-shaped fences eliminate escape success on four pure-action vectors (fs_enumerate, net_boundary, syscall_boundary, state_immutability) regardless of model capability or vendor — pooled symbiont success is **0% (0/560 trials)** across these four. On the orchestrator-injection vector at the tightened n=200, the typed `agent_summary` validator + expanded marker list reduces escape from **59% to 3.6% pooled** (excluding GPT-5: **52% to 1.5%**). The Docker-sandboxed substrate provides material protection only on syscall_boundary (38% → 9%, because the lure binary is missing from the container image), confirming the original spec's hypothesis: typestate-shape and OS-isolation-shape defenses address independent threat classes. **GPT-5 is now identified as a model-specific outlier** at 16% [12%, 22%] symbiont bypass on injection — its paraphrase aggression exceeds what literal-substring marker matching can defend against. Total ground-truth spend $37.02 for the v5 sweep; cumulative $50.89 across all sweeps on this branch.
