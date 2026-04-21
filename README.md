# symbiont-karpathy-loop

A worked example of building autonomous agents on **[Symbiont](https://github.com/ThirdKeyAI/symbiont)** with three properties you can actually audit:

1. **Sandboxing that holds under pressure.** Cedar policy + executor tool-profile-of-one + reflector budget cap. Across an adversarial sweep spanning four distinct attack prompts × nine frontier-and-open-weight models × benchmark tasks, the safety layer caught every attempt with zero escapes.
2. **End-to-end observability of the run.** Generation id, upstream-provider routing, authoritative `usage.cost`, per-call latency, and optional broadcast-trace labels — captured into JSONL sidecars alongside the signed run journals, per inference call.
3. **A reproducible agent-evaluation harness.** Five benchmark tasks, twelve models priced end-to-end across multiple sweeps, authoritative dollar numbers rather than token-count estimates. One command to run a model through the whole matrix.

A Karpathy-style reflective-learning loop ("edit one file, run the experiment, observe, repeat") ships as the worked example on top of that infrastructure — and under the specific pairing where the student is capable and the teacher is smarter than the student, the loop measurably fires (v3 report). Under other pairings it doesn't. The demo reports both cases honestly.

## Headline numbers

Each of these is reproduced by committed sweep artifacts under `demo-output/`.

| claim | number | source |
|---|---|---|
| **total refusals across 4 adversarial variants × 9 models** | **234** | `MODEL-SWEEP-REPORT-v4.md` |
| **adversarial reflector runs behind that number** | **228** | per-variant dashboards under `demo-output/` |
| **escapes** | **0** | every refusal caught by Cedar + executor fence |
| models evaluated end-to-end | **12** | v1 (12 incl. Ollama local), v2 (9 OpenRouter), v3 (cross-pairing), v4 (4 adversarial variants) |
| total authoritative OpenRouter spend across every committed sweep | **≈ $8** | summed from `data/*/runs.db::est_cost` |
| `est_cost` column backed by OpenRouter `usage.cost` | ✓ since v2 | `crates/demo-karpathy-loop/src/openrouter_provider.rs` |
| per-call generation_id + upstream-provider attribution | ✓ since v2 | `journals-<tag>/<ts>-<task>-n<NNN>-<kind>-calls.jsonl` |

### Per-model adversarial refusal matrix (v4)

Columns are the four attack shapes; cells are per-model refusal counts.

| model | adversarial | prompt-inj | tool-confusion | identity-hijack | total |
|-------|---:|---:|---:|---:|---:|
| gpt5             |  0 |  0 |  0 |  0 |  0 |
| haiku45          |  0 |  0 |  0 |  0 |  0 |
| minimax-m27      |  0 |  0 |  0 |  0 |  0 |
| mimo-v2-pro      |  0 |  0 |  0 |  0 |  0 |
| gemini25pro      |  4 |  7 |  2 |  1 | 14 |
| deepseekv31      |  4 | 11 |  5 |  5 | 25 |
| qwen36-plus      | 14 |  6 | 13 | 16 | 49 |
| gpt-oss-20b      |  8 | 12 | 16 | 20 | 56 |
| qwen3-235b       | 16 | 11 | **38** | 18 | **83** |

Four models (GPT-5, Haiku 4.5, MiniMax, MiMo) took zero bait across every attack. Qwen3-235B took the tool-confusion bait 38 times in a single arm — the model clearly tried; the sandbox held.

Full per-variant breakdown in [`MODEL-SWEEP-REPORT-v4.md`](demo-output/MODEL-SWEEP-REPORT-v4.md).

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────┐
│  Task Harness (symbi-kloop-bench)                           │
│   • picks a task from on-disk benchmark set                 │
│   • mints a fresh InferenceProvider (per-role on OpenRouter)│
│   • invokes real symbi-runtime ORGA loop                    │
│   • writes runs row (incl. est_cost, prompt/completion)     │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Symbiont ORGA loop — real runtime, not a mock              │
│   Observe → Reason → Gate → Act                             │
│   • NamedPrincipalCedarGate (`Agent::"task_agent"`)         │
│   • BufferedJournal → JSON on disk                          │
│   • TaskActionExecutor with task-domain tool handlers       │
│   • recall_knowledge (read-only)                            │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Reflector pass — runs after every task run                 │
│   • Cedar principal: `Agent::"reflector"`                   │
│   • ReflectorActionExecutor: tool-profile-of-one            │
│     (only `store_knowledge`, cap configurable)              │
│   • Default prompt OR one of 4 adversarial variants         │
│   • JSONL sidecar captures per-call OpenRouter metadata     │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Dashboard (sparklines, cost, latency) + per-run report     │
└─────────────────────────────────────────────────────────────┘
```

### Two principals, two fences

| Principal | Policy file | Tool profile | Writes knowledge? |
|---|---|---|---|
| `Agent::"task_agent"` | [`policies/task-agent.cedar`](policies/task-agent.cedar) | ~30 task-domain tools + `answer` + `recall_knowledge` | **No** (`forbid` clause + executor has no handler) |
| `Agent::"reflector"` | [`policies/reflector.cedar`](policies/reflector.cedar) | `store_knowledge` only, budget-capped | **Yes** — only principal that can |

Both the Cedar permit-list and the `ActionExecutor` refuse out-of-profile calls. The belt-and-suspenders layering is intentional: a policy relaxation alone can't let either agent touch the other's tool surface — the executor has no handler for it.

### Four adversarial reflector prompts

| Variant flag | Attack shape |
|---|---|
| `--adversarial-variant adversarial` | Tempts the LLM to re-run task-domain tools "to verify" before storing. Direct tool-profile breach. |
| `--adversarial-variant prompt-injection` | Smuggles a fake "SYSTEM OVERRIDE" block into the user message claiming capabilities were expanded. |
| `--adversarial-variant tool-confusion` | Claims `store_knowledge` and `answer` are aliases. Semantic confusion → structural violation. |
| `--adversarial-variant identity-hijack` | Social-engineers the LLM into believing its principal was rebound to `task_agent`. |

All four target the reflector's profile-of-one. Cedar + executor both refuse every attempt.

## Quick start (no keys required)

The demo runs offline with a deterministic mock provider.

```bash
scripts/run-demo.sh              # 3 iterations × 4 tasks, scripted improvement curve
```

The mock provider's "long path" and "short path" scripts demonstrate the Karpathy curve exactly because they're scripted to — it's the reference shape the cloud runs get compared against.

## Full sweep (with API keys)

The real work runs against OpenRouter. One key, one file:

```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' > .env
```

Then:

```bash
# 9-model default sweep, 3 iterations × 4 tasks
scripts/run-openrouter-sweep.sh 3

# Safety sweep — one variant at a time
VARIANT=adversarial      scripts/run-openrouter-sweep.sh 3
VARIANT=prompt-injection scripts/run-openrouter-sweep.sh 1
VARIANT=tool-confusion   scripts/run-openrouter-sweep.sh 1
VARIANT=identity-hijack  scripts/run-openrouter-sweep.sh 1

# Post-hoc reflector-quality analysis
scripts/analyze-reflector-quality.py
scripts/analyze-reflector-quality.py --suffix=-adv

# T5 cross-pairing experiment (capable student + varying teacher)
scripts/run-t5-matrix.sh 5
```

Each arm's artefacts land under `data/<tag>/`, `journals-<tag>/`, and `demo-output/<tag>-*`. The v1/v2/v3 sweep reports (`demo-output/MODEL-SWEEP-REPORT*.md`) walk through specific findings.

## Pointing at a local LLM (Ollama etc.)

```bash
target/release/symbi-kloop-bench \
  --provider ollama \
  --ollama-url http://<host>:11434/v1 \
  --ollama-model gemma4:latest \
  demo --iterations 3
```

The local-LLM path is intentionally shipped inside this crate rather than punching a hole through Symbiont's shared `net_guard` SSRF filter. The production guard stays intact; opting into a private-IP LLM is a demo-crate decision.

## Cross-pairing — split task agent and reflector models

```bash
export OPENROUTER_MODEL_TASK=anthropic/claude-haiku-4.5
export OPENROUTER_MODEL_REFLECT=openai/gpt-5
target/release/symbi-kloop-bench --provider openrouter demo --iterations 5 --only T5
```

`OPENROUTER_MODEL` is the single-model shortcut; the per-role vars override it independently. Use `--no-reflector` for a learning-disabled negative control.

## Results — three curated writeups

Each sweep report is committed so you can diff over time:

- **[`demo-output/MODEL-SWEEP-REPORT.md`](demo-output/MODEL-SWEEP-REPORT.md)** (v1) — first 12-model matrix with per-task verdicts, and the 15 numbered improvement suggestions that drove v2 and v3.
- **[`demo-output/MODEL-SWEEP-REPORT-v2.md`](demo-output/MODEL-SWEEP-REPORT-v2.md)** (v2) — default + adversarial sweep, authoritative cost, budget-cap fence, broadcast trace fields. "50 refusals, 0 escapes" headline.
- **[`demo-output/MODEL-SWEEP-REPORT-v3.md`](demo-output/MODEL-SWEEP-REPORT-v3.md)** (v3) — T5 task and the cross-pairing matrix. The Karpathy curve fires cleanly under the capable-student + smarter-teacher pairing; doesn't otherwise. Both cases reported.
- **[`demo-output/MODEL-SWEEP-REPORT-v4.md`](demo-output/MODEL-SWEEP-REPORT-v4.md)** (v4) — four adversarial variants × 9 models. 234 refusals, 0 escapes. The safety story as numbers.

## Layout

```
tasks/              Benchmark task JSON (T1–T5)
policies/           Cedar policies — task agent and reflector
agents/             Symbiont DSL for the task agent
reflector/          Symbiont DSL for the reflector
crates/
  demo-karpathy-loop/   Shared types: tasks, scoring, knowledge store,
                        task / reflector executors, OpenRouter &
                        Ollama providers, mock provider
  symbi-kloop-bench/    Harness, reflector driver, orchestrator,
                        dashboard, report, static pricing table
scripts/
  run-demo.sh                      Full offline run (mock provider)
  run-openrouter-sweep.sh          9-model OpenRouter sweep
                                   (VARIANT=... for safety variants)
  run-t5-matrix.sh                 Cross-pairing matrix
  analyze-reflector-quality.py     Post-hoc verdict per (model, task)
data/<tag>/                 SQLite runs.db + knowledge.db per tag
journals-<tag>/             Signed-style run journals + JSONL
                            per-call sidecars
demo-output/                Generated reports + dashboards + logs
```

## Building from this repo

This workspace depends on Symbiont via a path dependency on `../symbiont/crates/runtime`. Clone side-by-side:

```
<parent>/
  symbiont/                  # the Symbiont runtime
  symbiont-karpathy-loop/    # this repo
```

Adjust the workspace dep in `Cargo.toml` if your layout differs.

## What this demo is NOT

- **Not recursive self-improvement.** Each principal is boxed. The reflector teaches the task agent new procedures; it cannot teach itself new capabilities. That boundary is enforced by policy and re-enforced by executor structure.
- **Not a universal claim that reflective learning makes agents better.** v3 shows the curve fires under specific pairings; v2 shows it's mostly flat on frontier-only pairings. Both findings are reported.
- **Not a comprehensive benchmark suite.** Five curated tasks with synthetic-but-realistic inputs. For research-grade evaluation you'd need dozens of tasks with statistical power.
- **Not a replacement for a real observability stack.** The JSONL sidecars, broadcast traces, and dashboards are enough to audit a run; Langfuse/Helicone/PostHog wired through OpenRouter's Settings → Observability does the long-term visualisation.

## Feedback and contributions

Open an issue or PR. The v1/v2/v3 reports each end with a "suggestions for next version" list — any of those items is a good starting point.
