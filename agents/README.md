# `agents/` — DSL definitions for each principal in the demo

The Symbiont runtime governs every agent through a small DSL that
declares the agent's permitted tools and its prompt seed. The DSL
is the source-of-record for what an agent is *allowed to attempt*;
Cedar (under `policies/`) is what enforces the rules at action
dispatch time.

| File | Principal |
|---|---|
| `task-agent.symbi` | The "student" — solves a benchmark task by calling diagnostic tools and committing an `answer`. The DSL declares its permitted tools (no `store_knowledge`, no `system_shell`). |

The reflector and delegator DSLs live alongside their respective
runtime modules under `reflector/` and `delegator/`.

## How the DSL meets Cedar

A tool call from any of these agents flows through three fences in
order:

1. **DSL profile** — the runtime parser refuses to wire up tools
   that aren't in the agent's DSL, so a malformed prompt asking
   for an out-of-profile tool never reaches the policy gate.
2. **Cedar policy gate** — `policies/<role>.cedar` is consulted
   for every action the loop intends to dispatch. Approve, deny,
   or modify-with-reason.
3. **Executor** — only handlers wired up for that principal are
   reachable; if the principal's executor has no handler for the
   call, the executor refuses (`executor_refused`) even if Cedar
   somehow approved.

See `MODEL-SWEEP-REPORT-v9.md` §7 for empirical evidence of all
three fences firing under the adversarial reflector prompt.
