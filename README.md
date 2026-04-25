# symbiont-orga-demo

> Working title: this repository will likely be renamed
> `symbiont-orga-demo` before going public. ORGA = the
> **observe-reason-gate-act** loop, which is what the runtime
> actually implements. The current name carries internal
> shorthand that's clearer to outsiders as "an ORGA loop demo".

A worked demo of bounded autonomous agents on
**[Symbiont](https://github.com/ThirdKeyAI/symbiont)** with three
auditable properties:

1. **Cedar policy enforcement holds under adversarial pressure.**
   In a sweep across nine frontier and open-weight models with
   the adversarial reflector prompt, the policy gate caught
   **263 attempted forbidden tool calls (242 Cedar + 21 executor
   cap), with zero successful escapes**. The gate is
   **compile-time unskippable** — five trybuild proofs in
   `crates/symbi-kloop-bench/tests/compile-fail/` show the type
   system rejects any source code that tries to dispatch a tool
   without going through the gate.
2. **Per-call observability**, captured into JSONL sidecars
   alongside signed run journals: generation id, upstream
   provider, authoritative `usage.cost`, latency, and an
   optional broadcast-trace label for downstream observability
   tooling.
3. **A reproducible agent-evaluation harness.** Five benchmark
   tasks, nine models priced end-to-end, real dollar costs
   rather than token-count estimates. One command runs a model
   through the whole matrix.

The Karpathy-style "edit-run-observe-repeat" reflective-learning
loop ships as the worked example on top of that infrastructure.
Under the right student/teacher pairing the loop measurably fires
(see `MODEL-SWEEP-REPORT-v9.md`); under others it doesn't. The
demo reports both honestly.

## Architecture in 30 seconds

```
                     ┌─────────────────────────────┐
                     │   delegator   (one tool:    │
                     │     pick_task)              │
                     └─────────────┬───────────────┘
                                   │ chooses task_id
                                   ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  task agent (probing tools + answer; no store_knowledge)     │
  │       │                                                      │
  │       ▼                                                      │
  │  Cedar policy gate  ⇆  executor profile-of-N                 │
  │       │                                                      │
  │       ▼                                                      │
  │  signed journal + per-call OpenRouter sidecar                │
  └──────────────────────────────────┬───────────────────────────┘
                                     │ tool trace + final answer
                                     ▼
        ┌────────────────────────────────────────────────────┐
        │  reflector  (one tool: store_knowledge)            │
        │       │                                            │
        │       ▼                                            │
        │  Cedar policy gate  ⇆  executor cap (5 / run)      │
        │       │                                            │
        │       ▼                                            │
        │  symbi-invis-strip sanitiser  ⇆  knowledge.db      │
        └────────────────────────────────────────────────────┘
```

Three principals, three Cedar policies, two fences per dispatch
(Cedar permit + executor handler/cap), one sanitiser between
the LLM and persistent storage.

## Quickstart — mock provider, no API key

This repo path-deps the Symbiont runtime, so clone both
repositories side-by-side (CI does the same; see
`.github/workflows/ci.yml`):

```bash
mkdir <parent> && cd <parent>
git clone https://github.com/ThirdKeyAI/symbiont
git clone https://github.com/ThirdKeyAI/symbiont-karpathy-loop
cd symbiont-karpathy-loop
cargo build -j2 --release
scripts/run-demo.sh        # 3 iterations × 5 tasks, $0
```

If you'd rather have a single-clone build, swap the runtime line
in workspace `Cargo.toml` from a path-dep to a `git`-dep pinned to
a SHA — exact line in `CONTRIBUTING.md`.

This runs end-to-end against a deterministic mock provider that
ships with the demo crate. Useful for CI and for understanding
the harness without spending money. See
`MODEL-SWEEP-REPORT-v9.md` §1 for an interpretation of the
mock-arm numbers and what they do and do not say.

## Quickstart — real models via OpenRouter

```bash
echo "OPENROUTER_API_KEY=sk-or-…"                        > .env
echo "OPENROUTER_MODEL=anthropic/claude-haiku-4.5"      >> .env
chmod 600 .env

# One model:
ONLY=haiku45 scripts/run-openrouter-sweep.sh 10

# All nine models in the sweep list:
scripts/run-openrouter-sweep.sh 10
```

The full nine-model sweep at 10 iterations runs ~5–6 hours wall
and cost **$8.70** in the v9 default arm and **$9.88** in the v9
adversarial arm (combined $18.58). Add `VARIANT=adversarial` for
the pressure-test arm. Per-model dbs land at
`data/<tag>/runs.db`; per-model markdown reports at
`demo-output/run-<tag>.md`.

## Aggregating results into paper-grade tables

```bash
target/release/symbi-kloop-bench --db data/<tag>/runs.db \
    perf --axis model --format md          # per-model headline
target/release/symbi-kloop-bench --db data/<tag>/runs.db \
    perf --axis task  --format csv         # per-task export
target/release/symbi-kloop-bench --db data/<tag>/runs.db \
    perf --axis termination --format json  # why-runs-fail breakdown
```

The `perf` subcommand is read-only SQL over the existing `runs`
table. It works on any historical `runs.db` whose companion
`tasks/` and `policies/` trees no longer exist (paper archives,
old sweep snapshots).

## How the safety story works

| Claim | Evidence |
|---|---|
| The Cedar gate is wired in. | `crates/symbi-kloop-bench/src/policy_gate.rs` plus 3 unit tests. |
| The Cedar gate refuses adversarial calls. | v9 §7: 242 Cedar refusals across 9 cloud models on the adversarial reflector prompt; 0 escapes. |
| The Cedar gate is **compile-time unskippable**. | 5 trybuild compile-fail proofs in `crates/symbi-kloop-bench/tests/compile-fail/` with pinned `.stderr` snapshots: skip-policy-check, observe-before-dispatch, builder-missing-provider, phase-marker-forgery, reuse-after-consumed. CI re-verifies every push. |
| Even a hypothetical policy relaxation can't escape. | Executor handler maps are profile-of-one for reflector and delegator; the second fence fired 21 times in the v9 adversarial sweep. |
| Sanitiser strips invisible/control chars before storage. | 243-code-point exhaustive sweep in the demo crate; 1 435-row post-sweep audit gate in CI. |
| Cedar policy files can't be weakened with homoglyphs. | `scripts/lint-cedar-policies.py` runs in CI on every push. |

## Reports

- **`demo-output/MODEL-SWEEP-REPORT-v9.md`** — current paper
  reference: perf aggregator, typestate compile-fail proofs,
  9-model cloud sweep (default + adversarial arms), wall time,
  budget, reproduce instructions.
- Older reports (`MODEL-SWEEP-REPORT-v1.md` through `v8.md`)
  capture the iteration history. Each report is self-contained
  and reproduces from the committed sweep artifacts where
  applicable.

## Layout

| Path | Purpose |
|---|---|
| `crates/demo-karpathy-loop/` | Mock provider, knowledge store, sanitiser application, task action executor. |
| `crates/symbi-invis-strip/` | Standalone sanitiser crate (exhaustive 243-code-point sweep + HTML-comment + Markdown-fence stripping). Reusable outside this demo. |
| `crates/symbi-kloop-bench/` | The harness. Subcommands: `run`, `demo`, `dashboard`, `report`, `perf`. |
| `agents/`, `reflector/`, `delegator/` | DSL definitions for each principal. |
| `policies/` | Cedar policies, one per principal. |
| `tasks/` | T1–T5 benchmark tasks (JSON). |
| `scripts/` | `run-demo.sh`, `run-openrouter-sweep.sh`, audit + lint Python. |
| `demo-output/` | Sweep reports + paper-ready CSV/JSON exports. |

## License

Apache-2.0. See `LICENSE`.

## Contributing

See `CONTRIBUTING.md`. The bar is "the change makes the safety
or evaluation story sharper", not "the change ships a feature".
