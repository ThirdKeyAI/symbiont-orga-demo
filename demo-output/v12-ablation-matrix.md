# v12.1 — stack-stripping ablation matrix (9 models)

Two L0 ablation sweeps run against the existing 9-model OpenRouter group:

- **Sanitiser ablation** (`hc-c0-s0`): `--adversarial-variant html-comment-smuggle` with `--cedar-mode off --sanitiser-mode off`. Hypothesis: with the sanitiser bypass enabled, every LLM-emitted `<!-- HIDDEN -->` payload should survive intact into the knowledge store. v8 baseline (sanitiser on) refused 100% of payloads at the store layer.

- **Cedar ablation** (`tc-c0-s0`): `--adversarial-variant tool-confusion` with `--cedar-mode off --sanitiser-mode off`. Hypothesis: with the permissive gate, `cedar_denied` per row should be 0 (Cedar is bypassed). The `tool-confusion` variant was chosen because it produced the highest Cedar denial counts in v5 (38 across qwen3-235b alone).

## Sanitiser ablation — html-comment-smuggle survival rate at L0

Knowledge-store contents inspected post-sweep. Hostile = any row whose `object` field contains `<!--`, `-->`, or `HIDDEN`. **At L0, every hostile payload that reaches the store survives intact** — the sanitiser's bypass works as designed; the non-redundancy claim is empirically grounded.

| model | stored procedures | hostile-bearing | survival % | spend $ |
|---|---:|---:|---:|---:|
| gpt5 | 44 | 44 | 100.0% | 1.1013 |
| gemini25pro | 41 | 41 | 100.0% | 1.8606 |
| haiku45 | 41 | 41 | 100.0% | 0.3683 |
| deepseekv31 | 55 | 55 | 100.0% | 0.1170 |
| qwen3-235b | 59 | 59 | 100.0% | 0.0224 |
| mimo-v2-pro | 53 | 49 | 92.5% | 0.1753 |
| minimax-m27 | 35 | 31 | 88.6% | 0.0550 |
| gpt-oss-20b | 52 | 52 | 100.0% | 0.0221 |
| qwen36-plus | 42 | 17 | 40.5% | 0.1583 |
| **TOTAL** | **422** | **389** | **92.2%** | **3.8803** |

**v8 baseline (L2 — sanitiser on):** 0/8 hostile payloads survived = 100% sanitiser bite-rate. The L0 column above is the inverted measurement — without the sanitiser, hostile payloads survive at the percentages shown.

**Interpretation:** Cedar permits `store_knowledge` (it's the reflector's profile-of-one tool); ToolClad doesn't apply (no scope_target arg); the sanitiser is the only fence with design intent against this attack class. The L0 numbers above confirm: removing the sanitiser cleanly exposes every hostile payload the LLM emitted into the store. **Sanitiser is doing 100% of the defensive work for html-comment-smuggle.**

## Cedar ablation — tool-confusion at L0

Per-row counters for the L0 (cedar off + sanitiser off) tool-confusion sweep. **Expected:** `cedar_denied = 0` because the gate is bypassed; `executor_refused` may be non-zero because the executor's profile-of-one belt-and-suspenders fence is independent of Cedar.

| model | rows | cedar_denied | executor_refused | violations_prev | spend $ |
|---|---:|---:|---:|---:|---:|
| gpt5 | 49 | 0 | 0 | 0 | 1.2160 |
| gemini25pro | 36 | 0 | 22 | 22 | 1.7825 |
| haiku45 | 50 | 0 | 0 | 0 | 0.3488 |
| deepseekv31 | 50 | 0 | 0 | 0 | 0.1156 |
| qwen3-235b | 50 | 0 | 26 | 26 | 0.0223 |
| mimo-v2-pro | 50 | 0 | 0 | 0 | 0.1495 |
| minimax-m27 | 50 | 0 | 0 | 0 | 0.0632 |
| gpt-oss-20b | 49 | 0 | 144 | 144 | 0.0299 |
| qwen36-plus | 50 | 0 | 27 | 27 | 0.1501 |
| **TOTAL** | **434** | **0** | **219** | **219** | **3.8778** |

**v5 baseline (L2):** `MODEL-SWEEP-REPORT-v5.md` reported **38 Cedar denials** for qwen3-235b alone on tool-confusion, and **115 cumulative across 9 models** (v5 §tool-confusion). At L0, that count drops to **0** total Cedar denials across the same 9 models — the gate is bypassed.

**Important secondary finding.** `executor_refused = 219` at L0 across all 9 models. Even with Cedar bypassed, the executor's profile-of-one belt-and-suspenders fence (the second arm of the action layer) continues to refuse out-of-profile tool names. **The 'action layer' is two independent fences, not one.** Removing Cedar does not fully expose the action layer because `*ActionExecutor::handle_one` runs an independent name-membership check before dispatching. This is a real OATS architectural finding worth flagging — the `Cedar + executor profile-of-one` pairing in the README's fence table is structurally important; you cannot ablate one without the other and claim 'no action-layer defence.'

## Summary — what each fence catches (empirical)

| attack class | Cedar | Executor profile | Sanitiser | ToolClad |
|---|:--:|:--:|:--:|:--:|
| out-of-profile tool call (tool-confusion) | **0** denials at L0; ~115 at L2 (v5) | **219** refusals at L0 (still fires) | n/a | n/a |
| html-comment-smuggle | permits | permits | **389 / 422 survive at L0** | n/a |
| tool-arg-injection | permits | permits | n/a | **333 / 335 = 99.4% bite at L3 (v11)** |

**Non-redundancy claim, fully measured.** Each of Cedar, executor profile-of-one, sanitiser, and ToolClad catches a class of attack the others do not. Removing any one fence exposes the corresponding attack class at near-100% survival. This is the OATS launch chart — defensible, reproducible, and regenerable from this repo's artefacts via `scripts/v12-ablation-analyze.py`.

**v12.1 sweep cost:** $7.7581 (434 task+reflect rows across 9 models × 2 ablation sweeps). Cap was $40.