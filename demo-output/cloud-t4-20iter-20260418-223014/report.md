# Karpathy loop demo report

Generated 2026-04-19 05:30:14 UTC from `data/runs.db`.

## Headline numbers

- Knowledge accumulated: **2 procedures**
- Policy violations prevented: **0** (reflector attempted non-`store_knowledge` tool calls; Cedar + the reflector's tool-profile-of-one refused each one)

## Per-task improvement

### T1 — K8s deployment root-cause triage

_No runs recorded for this task yet._

### T2 — Support ticket classifier

_No runs recorded for this task yet._

### T3 — Dependency-upgrade safety review

_No runs recorded for this task yet._

### T4 — rustc compile-error classifier

| Run | Score | Iterations | Tokens | Termination |
|----:|------:|-----------:|-------:|:------------|
| 1 | 1.00 | 4 | 5602 | completed |
| 2 | 1.00 | 4 | 5615 | completed |
| 3 | 1.00 | 4 | 5682 | completed |
| 4 | 1.00 | 4 | 5686 | completed |
| 5 | 1.00 | 4 | 5678 | completed |
| 6 | 1.00 | 4 | 5690 | completed |
| 7 | 1.00 | 4 | 5692 | completed |
| 8 | 1.00 | 4 | 5683 | completed |
| 9 | 1.00 | 4 | 5687 | completed |
| 10 | 1.00 | 4 | 5680 | completed |
| 11 | 1.00 | 4 | 5682 | completed |
| 12 | 1.00 | 4 | 5682 | completed |
| 13 | 1.00 | 4 | 5675 | completed |
| 14 | 1.00 | 4 | 5687 | completed |
| 15 | 1.00 | 4 | 5687 | completed |
| 16 | 1.00 | 4 | 5678 | completed |
| 17 | 1.00 | 4 | 5671 | completed |
| 18 | 1.00 | 4 | 5684 | completed |
| 19 | 1.00 | 4 | 5677 | completed |
| 20 | 1.00 | 4 | 5677 | completed |

**Delta (run 1 → 20):** score +0.00, iterations +0, tokens +75

## Policy posture

Every tool call went through a Cedar policy gate keyed on a stable principal label (`Agent::"task_agent"` or `Agent::"reflector"`). The reflector's policy permits exactly one tool — `store_knowledge` — and explicitly forbids every other `tool_call::*`. The reflector's executor layer adds belt-and-suspenders: even a hypothetical policy relaxation can't let the reflector touch the task agent's tool vocabulary.

- Task agent policy: [`policies/task-agent.cedar`](../policies/task-agent.cedar)
- Reflector policy: [`policies/reflector.cedar`](../policies/reflector.cedar)

## What this demo is not

This is not recursive self-improvement. The agent gets better at its *assigned task* within a Cedar-bounded envelope. Every improvement is a signed journal entry. The reflector can teach the task agent new procedures; it cannot teach itself new capabilities.
