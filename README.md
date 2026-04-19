# symbiont-karpathy-loop

Karpathy's dev loop — edit one file, run the experiment, observe, repeat — applied to autonomous agent execution. A Symbiont agent gets measurably better at an assigned task over successive runs; every improvement is a signed journal entry; every action passes through a Cedar gate.

## What this demo shows

- A **task agent** solves one of three benchmark tasks (sort-by-rate, minimum spanning tree, or string classification). Each task has multiple valid solution paths of uneven efficiency, so there's real room for the agent to learn.
- A **reflector agent** runs after every task attempt and proposes zero to five concrete procedures the task agent should remember for similar future tasks. The reflector can only call `store_knowledge`. Cedar enforces the boundary; the reflector's action executor has no handler for any other tool as a second line of defence.
- Across successive runs of the same task the agent's **iteration count drops, token count drops, and score holds or improves** — the legible improvement signal Karpathy's ML dev loop runs on, transplanted into autonomous agent execution.
- The reflector attempts some scripted "cheat" tool calls (calling `answer`, `recall_knowledge`, or task-domain tools) during the demo. Cedar refuses every one and the counter surfaces on the dashboard and in the report as **policy violations prevented** — the safety punch line.

## What this demo is NOT

Not recursive self-improvement. The agent gets better at its *assigned task* within a Cedar-bounded envelope. The reflector can teach the task agent new procedures; it cannot teach itself new capabilities. That boundary is enforced by policy and re-enforced by executor structure.

## Quick start

The demo runs offline with a deterministic mock provider; no API key required.

```bash
# 3 iterations × 3 tasks, scripted improvement curve
scripts/run-demo.sh

# or run one iteration by hand:
cargo run -p symbi-kloop-bench -- demo --iterations 3
cargo run -p symbi-kloop-bench -- dashboard
cargo run -p symbi-kloop-bench -- report --out demo-output/run-latest.md
```

To swap in a real cloud LLM (requires `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`):

```bash
PROVIDER=cloud scripts/run-demo.sh 5
```

## Layout

```
tasks/          Benchmark task JSON. Three tasks with multiple solution
                paths of uneven efficiency.
policies/       Cedar policies — task agent and reflector.
agents/         Symbiont DSL for the task agent.
reflector/      Symbiont DSL for the reflector (tool profile of one).
crates/
  demo-karpathy-loop/   Shared types: tasks, scoring, knowledge store,
                        task-agent and reflector executors, and the
                        deterministic mock inference provider.
  symbi-kloop-bench/    The harness, reflector driver, orchestrator,
                        dashboard, and proof-artifact generator.
scripts/run-demo.sh     End-to-end orchestrator.
demo-output/            Generated proof artifact lands here.
```

## Safety story

- The reflector's Cedar policy permits exactly one tool (`store_knowledge`) and explicitly forbids every other `tool_call::*`.
- The reflector's `ActionExecutor` has no handler for anything other than `store_knowledge`; even a policy relaxation wouldn't let it call other tools.
- The task agent is read-only on the knowledge store (`recall_knowledge`) and has no `store_knowledge` handler.

## Building from this repo

This workspace depends on Symbiont via a **path dep** on `../symbiont/crates/runtime`. Clone both repos side-by-side:

```
<parent>/
  symbiont/                   # the Symbiont runtime
  symbiont-karpathy-loop/     # this repo
```

Adjust `Cargo.toml`'s workspace dep if your layout differs.
