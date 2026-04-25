# `reflector/` — the "teacher" agent

The reflector runs after each task-agent attempt. It reads the
task agent's tool trace and outcome, decides whether a procedure
is worth committing to long-term memory, and writes one or more
procedures via `store_knowledge`. **The reflector's only
permitted tool is `store_knowledge`.** It cannot probe, answer,
or shell out — by DSL profile, by Cedar policy, and by executor
handler map.

## Files

| File | Purpose |
|---|---|
| `reflector.dsl` | DSL declaration of the reflector's profile-of-one. Loaded by the runtime at startup. |

The Cedar policy that backs the reflector is at
`policies/reflector.cedar`. The two are deliberately co-designed:
the DSL says "this is the only tool I have", the Cedar policy
says "this is the only tool I am permitted to call". Either alone
would be a single-fence design; both is the dual-fence guarantee
that the v6/v7/v8/v9 sweep reports cite.

## Why the reflector is the highest-pressure surface

Every adversarial prompt variant in `crates/symbi-kloop-bench/src/reflector.rs`
(prompt-injection, tool-confusion, identity-hijack, homoglyph,
multi-stage, ciphered, non-english, paraphrase,
html-comment-smuggle, markdown-fence) is aimed at *the reflector
prompt*. The reasoning: the reflector lives downstream of the
task agent's output, which makes it the natural injection point
for an attacker who controls a tool's response. The v9 §7
adversarial sweep across 9 cloud models recorded **242 Cedar
refusals + 21 executor refusals = 263 attempted forbidden tool
calls, 0 successful escapes**.

## Per-run cap

`--reflector-store-cap N` (default 5) caps how many procedures
the reflector may write per pass. This is the executor-side fence
that fired 16 times on Gemini 2.5 Pro reflector in the v9
default sweep — the model was *legitimately* enthusiastic, the
cap kept the dataset honest. See v9 §6.3.
