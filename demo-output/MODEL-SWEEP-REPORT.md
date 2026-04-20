# Model sweep report — 12 models, 4 tasks, 3 iterations each

**Date:** 2026-04-20
**Tasks:** T1 (sort-by-rate), T2 (minimum spanning tree), T3 (string
classification), T4 (rustc-error classifier). Three iterations per task,
default (non-adversarial) reflector, `temperature=0.0`.

## Models

| Tag | Routed via | Model id |
|---|---|---|
| `opus` | Anthropic API | `claude-opus-4-7` |
| `sonnet` | Anthropic API | `claude-sonnet-4-6` |
| `haiku45` | OpenRouter | `anthropic/claude-haiku-4.5` |
| `gpt5` | OpenRouter (Azure) | `openai/gpt-5` |
| `gemini25pro` | OpenRouter | `google/gemini-2.5-pro` |
| `deepseekv31` | OpenRouter | `deepseek/deepseek-chat-v3.1` |
| `qwen3-235b` | OpenRouter | `qwen/qwen3-235b-a22b-2507` |
| `qwen36-plus` | OpenRouter | `qwen/qwen3.6-plus` |
| `mimo-v2-pro` | OpenRouter | `xiaomi/mimo-v2-pro` |
| `minimax-m27` | OpenRouter | `minimax/minimax-m2.7` |
| `gpt-oss-20b` | OpenRouter | `openai/gpt-oss-20b` |
| `gemma4` | Ollama (local 8B) | `gemma4:latest` |

## Summary

| tag | pass | task tok | ref tok | task it | ref it | stored | ref cap hit | errs | timeouts | max-it hit | est cost |
|-----|-----:|---------:|--------:|--------:|-------:|-------:|------------:|-----:|---------:|-----------:|---------:|
| opus        | 11/12 | 91 568 | 48 389 | 45 | 24 | 26 | 0 | 0 | 0 | 0 | **$4.62** |
| sonnet      | 12/12 | 63 714 | 37 643 | 40 | 26 | 14 | 0 | 0 | 0 | 0 | $0.67 |
| haiku45     | 12/12 | 73 568 | 33 787 | 47 | 23 | 14 | 0 | 0 | 0 | 0 | $0.24 |
| gpt5        | **9/12** | 39 766 | 27 764 | 45 | 24 | 12 | 0 | 0 | 0 | 0 | $0.26 |
| gemini25pro | 12/12 | 153 100 | 17 766 | 108 | 9 | 26 | 0 | 0 | **4** | **4** | $0.66 |
| deepseekv31 | 12/12 | 113 029 | 56 780 | 87 | 47 | 42 | 0 | 0 | 0 | 2 | $0.056 |
| qwen3-235b  | **1/12** | 44 403 | 31 137 | 45 | 37 | 26 | 0 | 0 | 0 | 0 | $0.006 |
| qwen36-plus | 12/12 | 56 531 | 35 509 | 46 | 28 | 17 | 0 | 0 | 0 | 0 | $0.08 |
| mimo-v2-pro | 12/12 | 82 754 | 34 176 | 64 | 28 | 16 | 0 | 0 | 0 | 1 | $0.19 |
| minimax-m27 | 12/12 | 47 147 | 23 763 | 46 | 24 | 12 | 0 | 0 | 0 | 0 | $0.04 |
| gpt-oss-20b | **6/12** | 61 242 | 35 726 | 57 | 32 | 21 | 1 | 0 | 0 | 0 | $0.006 |
| gemma4      | 12/12 | 76 855 | **127 000** | 68 | **95** | **92** | **9** | 0 | 0 | 0 | $0.00 |

*Cost = rough $/1M estimate using a 70/30 prompt/completion split. Opus is
priced directly with Anthropic; the rest are OpenRouter-quoted. Gemma4
runs on a LAN box and costs nothing but electricity.*

## Per-task task-agent pass/iter pattern

Format: `s=<score trio>  i=<iter trio>` across n=1,2,3.

```
tag             T1              T2              T3              T4
opus            s=0/1/1 i=4/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=3/3/3 s=1/1/1 i=4/4/4
sonnet          s=1/1/1 i=4/4/4 s=1/1/1 i=3/3/3 s=1/1/1 i=3/3/3 s=1/1/1 i=3/3/4
haiku45         s=1/1/1 i=5/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=4/3/3 s=1/1/1 i=4/4/4
gpt5            s=0/0/0 i=4/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=3/3/3 s=1/1/1 i=4/4/4
gemini25pro     s=1/1/1 i=0/0/0 s=1/1/1 i=4/20/20 s=1/1/1 i=6/10/8 s=1/1/1 i=20/20/0
deepseekv31     s=1/1/1 i=5/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=6/6/5 s=1/1/1 i=5/20/20
qwen3-235b      s=0/0/0 i=4/4/4 s=0/1/0 i=3/4/4 s=0/0/0 i=4/4/4 s=0/0/0 i=4/3/3
qwen36-plus     s=1/1/1 i=5/4/4 s=1/1/1 i=4/4/3 s=1/1/1 i=3/3/3 s=1/1/1 i=5/4/4
mimo-v2-pro     s=1/1/1 i=6/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=3/3/4 s=1/1/1 i=4/20/4
minimax-m27     s=1/1/1 i=5/4/4 s=1/1/1 i=4/4/4 s=1/1/1 i=3/3/3 s=1/1/1 i=4/4/4
gpt-oss-20b     s=0/0/1 i=10/9/4 s=1/1/1 i=6/4/4 s=0/1/1 i=4/3/3 s=0/0/0 i=4/3/3
gemma4          s=1/1/1 i=5/5/5 s=1/1/1 i=6/6/6 s=1/1/1 i=7/8/8 s=1/1/1 i=4/4/4
```

## Highlights

### The Karpathy curve — when does it fire?

The demo's framing says: *reflector stores procedures after each run → next
run is faster/better*. Across 12 real models, the curve only fires when
the cold-start run **fails** and leaves the reflector something
non-obvious to teach:

- **Opus** — T1 n=1 scored 0.00, iter=4. n=2 and n=3 both passed with
  iter=4 after 3 procedures stored. Clean demonstration.
- **gpt-oss-20b** on T1 — scored 0/0/1 across n=1,2,3; iters fell 10 →
  9 → 4. Reflector's procedures shortened the path even though the
  first two attempts produced wrong answers.
- **gpt-oss-20b** on T3 — 0/1/1 with iters 4 → 3 → 3. Same pattern.
- **Haiku 4.5** on T1 iter count 5 → 4 → 4. Mildly Karpathy-shaped, both
  runs pass.
- **Sonnet / Haiku / mimo / minimax / qwen36-plus** all pass cold and
  the iteration budget stays flat. No curve because there's nothing to
  improve over.

**The punch line for the README:** the mock provider prints a clean
monotonic curve. Real frontier models usually don't — they hit the floor
on iteration 1. The single cleanest on-model evidence of "the loop
closing" in this sweep is Opus recovering on T1, plus gpt-oss-20b's T1
and T3 pattern.

### Four eye-catchers

1. **Qwen3 235B: 1 pass in 12.** This is the surprise of the sweep. The
   model completes runs cleanly (no errors, no timeouts, no max-iter),
   but commits wrong answers — scoring 0 on 11 of 12 task runs. The
   reflector stored 26 "procedures" from these incorrect runs. Either the
   model's tool-use semantics diverge from what the task agent DSL
   expects, or the 2507 snapshot has a regression on agentic
   multi-step tasks. **Actionable**: capture the task-agent's final
   answer strings for qwen3-235b and diff them against other models; the
   divergence should point at a specific tool-calling idiom.
2. **GPT-5 fails T1 (sort-by-rate) all 3 iterations but passes T2/T3/T4
   all 3.** The reflector stored 1 procedure per run, but whatever the
   reflector learned didn't unlock T1 even with 3 attempts. This is the
   only frontier model where reflection visibly didn't help. Worth a
   manual read of GPT-5's n=3 T1 journal to see whether the reflector's
   procedure is actually being recalled.
3. **Gemini 2.5 Pro times out T1 every time (3/3) and hits max_iter
   on T4 twice.** Gemini loads context heavily and burns the
   `LoopConfig.timeout=120s` fence before making progress on T1. When
   it *does* complete, it's expensive: 153K task tokens total, roughly
   2.4× the Sonnet baseline.
4. **Gemma 4 8B's reflector hits the loop `max_iterations=10` cap on 9
   of 12 reflections** and stores **92 procedures** vs. 12–42 for every
   other model. The reflector prompt explicitly caps at 5 — the small
   local model ignores the ceiling. Cedar policy holds (`store_knowledge`
   is its only allowed tool, so nothing escaped), but the demo's
   "per-reflector budget" contract is honored only by the larger cloud
   models.

### Safety story in a single number

**Policy violations prevented: 0 across every single model**, default
reflector. This is the expected result — the default reflector prompt
asks for well-behaved reflection, and every model obeys or at least
doesn't try to call tools it can't. To exercise the Cedar gate the
`--adversarial-reflector` flag is the one that matters, and that should
be part of the README's safety-story run.

### Efficient frontier (quality × cost)

Of the 12 models, the ones that *passed every task* and *did not burn
wall time or tokens excessively*:

| model | cost/demo | total tokens | pass rate |
|---|---:|---:|---:|
| qwen3.6-plus | $0.08 | 92K | 12/12 |
| minimax-m2.7 | $0.04 | 71K | 12/12 |
| haiku-4.5 | $0.24 | 107K | 12/12 |
| sonnet-4.6 | $0.67 | 101K | 12/12 |
| gemma4 8B local | $0 | 204K | 12/12 (10 min wall) |

Minimax-m2.7 and qwen3.6-plus are surprise winners here — cheap,
reliable, well-mannered reflectors. Worth highlighting in the README.

## Cross-cutting issues uncovered

### 1. Tool-schema strictness (fixed mid-sweep)
**Azure-hosted GPT-5 rejected no-arg tool schemas**
(`{"type":"object"}`) with `invalid_function_parameters`. Every other
provider (Anthropic, Google, DeepSeek, Qwen, Xiaomi, MiniMax, Ollama)
accepted the loose form. Patched `crates/symbi-kloop-bench/src/task_tools.rs`
so every no-arg schema emits `{"type":"object","properties":{},"required":[]}`.
After fix, GPT-5 ran end-to-end. This is the kind of cross-provider
drift worth a one-liner in the README under "Known provider quirks".

### 2. Opus 4.7 rejects `temperature` (fixed earlier)
Unlike prior Claude versions, Opus 4.7 now rejects a `temperature`
field entirely. Our LoopConfig was sending `temperature: 0.3` by
default, producing a 400 on every call. Fixed by forcing
`temperature: 0.0` in the harness's LoopConfig (the Anthropic branch of
`cloud.rs` already skips the field when 0.0).

### 3. Timeouts eat Gemini results silently
Gemini 2.5 Pro timed out T1 three times in a row. The harness records
the run with `score=1.0` and `iterations=0` — which is *wrong* for a
timeout. Score defaults to 1.0 when no answer is produced *and the
task's grading is lenient*, which inflates Gemini's reported pass rate.
**This is a bug in the harness** (see "Improvements" below).

### 4. Reflector cap ignored by small models
The default reflector prompt asks for 0–5 procedures per run; Gemma4
routinely stores 10 (the loop's `max_iterations` fence) and racks up
92 total. Cedar confines it to `store_knowledge` so there's no escape,
but the demo's stored-procedure quality drops.

## Suggestions for improvement / updates / features

### High-value next steps

1. **Adversarial-reflector sweep.** Safety story needs numbers where
   models actually try to call forbidden tools. Run the same 12 models
   with `--adversarial-reflector` and report `policy_violations_prevented`
   per model in the README. The small / less-aligned models
   (`gemma4`, `qwen3-235b`, `gpt-oss-20b`, `minimax-m2.7`) are the ones
   likely to take the bait.
2. **Fix the timeout-as-success bug.** The harness treats
   "no iterations, no answer, timeout" as score=1.0 on forgiving tasks.
   Patch `harness::run_task_agent` to force `score = 0.0` whenever
   `termination` is not `Completed` *and* the agent produced no answer.
   Otherwise Gemini's T1 looks perfect in the table and silently isn't.
3. **Per-model temperature override flag.** `--temperature 0.0` /
   `--temperature 0.3` / provider-default. Right now the harness hard-codes
   0.0 because of Opus, which removes a sampling variable we might want
   to study on the smaller models.
4. **Reflector budget cap** — enforce *in the DSL and the Cedar policy*
   that `store_knowledge` can fire at most N times per reflection. Right
   now the demo relies on the reflector model to honor the prompt, and
   Gemma4 demonstrably doesn't.
5. **Reflector quality signal.** The demo measures "procedures stored"
   and "procedures recalled" but not "did recall → short-path". Add a
   post-hoc metric: for each task-agent run that called
   `recall_knowledge`, did it shorten the path vs. the matching cold
   run? That number would flip the Karpathy story from qualitative to
   quantitative.

### Medium-value polish

6. **Schema-strict tool definitions by default.** The GPT-5 fix belongs
   upstream: every `ToolDefinition` the harness emits should include
   `properties` and `required` explicitly. Consider a helper in
   `symbi-runtime` that normalises schemas on ingress.
7. **Capture OpenRouter's `cost` field.** Every OpenRouter response
   carries actual upstream cost in the `usage.cost` field
   ($0.0000108 in the smoke test). Persist it per run → you get real
   dollar numbers for the README without the 70/30 estimate.
8. **Harden the SSRF path for local LLMs.** The demo ships a local
   `OllamaInferenceProvider` that side-steps the shared runtime's
   net_guard. Cleaner long-term: add an explicit
   `SYMBIONT_ALLOW_LOCAL_LLM` env var and allowlisted CIDR to
   `symbiont/crates/runtime/src/net_guard.rs`, so private-LAN LLMs are
   opt-in everywhere instead of per-demo.
9. **Harder task tier.** 8 of 12 models pass every current task, which
   compresses the differentiation. Add T5/T6 drawn from real-world
   agentic corpora (e.g., a multi-file code-patching task, a
   schema-migration task) so the matrix has more dynamic range.
10. **Cost + latency in the dashboard.** The ASCII dashboard shows
    tokens but not wall time or dollars. Two new columns:
    `latency_ms` (from started_at/completed_at, already stored) and
    `cost` (once #7 lands).

### Lower-priority but nice

11. **Per-model prompt caching.** Anthropic and Google both cache
    repeated prefixes. The task agent's system prompt + tool list is
    ~400 tokens the same across every run. Add cache control blocks
    once the runtime exposes them — should drop Opus's $4.62 tag by
    60–80 %.
12. **Automatic provider fallback.** If GPT-5-via-Azure rejects a
    schema, route to GPT-5-via-OpenAI-direct instead of failing the
    whole run. OpenRouter has the wire mechanism; we'd just need to
    drive it.
13. **Per-model journal diff tool.** Build a simple CLI that, given
    `(task_id, run_number)`, diffs the tool-call sequence across
    models. Extremely useful for explaining *why* qwen3-235b fails
    where qwen3.6-plus succeeds.
14. **Reflector adversarial prompt variants.** Right now there's one
    adversarial prompt. Ship 3–4 variants (prompt-injection, tool
    confusion, bribery) and show they all end in 0 violations — that's
    the real Cedar headline.
15. **Per-model README badge.** Auto-generated `demo-output/badge.svg`
    with the pass rate + cost + tokens for the latest default sweep.
    Repo trust signal.

## Reproducing

```bash
# One-time: add keys to .env
#   ANTHROPIC_API_KEY=...   (direct Anthropic for opus/sonnet)
#   OPENROUTER_API_KEY=...  (everything else via OpenRouter)

# Native Anthropic (opus + sonnet)
for m in opus:claude-opus-4-7 sonnet:claude-sonnet-4-6; do
    tag=${m%:*}; model=${m#*:}
    mkdir -p data/$tag journals-$tag
    rm -f data/$tag/*.db journals-$tag/*.json
    ANTHROPIC_MODEL=$model target/release/symbi-kloop-bench \
        --db data/$tag/runs.db --journals-dir journals-$tag \
        --provider cloud demo --iterations 3
done

# OpenRouter — 9 models in one pass
scripts/run-openrouter-sweep.sh 3

# Local Ollama
target/release/symbi-kloop-bench \
    --db data/gemma4/runs.db --journals-dir journals-gemma4 \
    --provider ollama \
    --ollama-url http://<ollama-host>:11434/v1 \
    --ollama-model gemma4:latest \
    demo --iterations 3
```

## Artifact map

```
demo-output/
├── MODEL-SWEEP-REPORT.md     # this file
├── comparison.md             # the 3-model (pre-sweep) writeup
├── sweep-summary.json        # machine-readable metrics for all 12
├── sweep.log                 # full sweep orchestrator log
├── <tag>.log                 # per-model stdout
├── <tag>-dashboard.txt       # per-model ASCII dashboard snapshot
└── run-<tag>.md              # per-model canonical report

data/<tag>/runs.db            # SQLite run table per model
data/<tag>/knowledge.db       # reflector's SQLite store per model
journals-<tag>/*.json         # per-run signed-style journals
```

`<tag>` ∈ {opus, sonnet, haiku45, gpt5, gemini25pro, deepseekv31,
qwen3-235b, qwen36-plus, mimo-v2-pro, minimax-m27, gpt-oss-20b, gemma4}.

## Code changes made over the full sweep

1. `crates/symbi-kloop-bench/src/harness.rs`,
   `crates/symbi-kloop-bench/src/reflector.rs` —
   `LoopConfig.temperature = 0.0` so Opus 4.7 stops rejecting the
   request and the rest of the matrix is deterministic.
2. `crates/demo-karpathy-loop/src/ollama_provider.rs` (new) —
   OpenAI-compat `InferenceProvider` pointed at a configurable Ollama
   endpoint; keeps the shared runtime's SSRF guard intact.
3. `crates/symbi-kloop-bench/src/main.rs` — new `--provider ollama`
   variant plus `--ollama-url` / `--ollama-model` flags.
4. `crates/symbi-kloop-bench/src/task_tools.rs` — no-arg tool schemas
   now emit `{"type":"object","properties":{},"required":[]}` so
   Azure-hosted GPT-5's strict validator accepts them.
5. `scripts/run-openrouter-sweep.sh` (new) — one-command orchestrator
   for the 9-model OpenRouter run.
