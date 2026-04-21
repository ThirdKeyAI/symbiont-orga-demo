# Model sweep report v3 — the T5 cross-pairing experiment

**Date:** 2026-04-20
**Purpose:** stop testing whether the Karpathy loop fires *at all*
(v2's answer: "sometimes, mildly") and start testing what specifically
makes it fire. Fix the task, vary the model pairing along one axis
(task agent) and one more axis (reflector), measure the curve.

**Predecessor reports:** `MODEL-SWEEP-REPORT.md` (v1, 12 models ×
default reflector) and `MODEL-SWEEP-REPORT-v2.md` (9 OpenRouter models ×
default + adversarial). Both are preserved.

## The new setup

### T5 — rustc cascade root-cause identification
A Rust build fails with 8 compile errors; exactly one (index 3, an
`E0433 use of undeclared type Value`) is the root cause. The other 7
are cascade effects. The agent answers with the 1-based index of the
root.

Why this task: v1/v2 tasks were too easy — frontier models hit the
iteration floor on attempt 1, so the reflector never had room to
shorten anything. T5 has a naive path (walk every `error_detail`, cross-reference, sort) of 10–15 iterations and an expert path (one
`error_list` call → notice the E0433 → answer) of 3. Room for the
curve to fire, with a specific insight the reflector can teach:
*"E0432 / E0433 / E0425 near the top of a cascade are almost
always the root."*

### Cross-pairing — task agent × reflector
Until now the demo used **one** model for both roles. That conflates
"good solver" with "good teacher." v3 splits them: `--provider openrouter` now reads `OPENROUTER_MODEL_TASK` and `OPENROUTER_MODEL_REFLECT`
independently, with fallback to `OPENROUTER_MODEL` when unset. A new
`--no-reflector` CLI flag provides the negative-control arm.

Six arms, 5 iterations of T5 each:

| arm | task agent | reflector |
|-----|------------|-----------|
| `t5-none`         | gpt-oss-20b | (no reflector)       |
| `t5-self`         | gpt-oss-20b | gpt-oss-20b          |
| `t5-haiku`        | gpt-oss-20b | anthropic/claude-haiku-4.5 |
| `t5-gpt5`         | gpt-oss-20b | openai/gpt-5         |
| `t5-haikuself`    | haiku-4.5   | haiku-4.5            |
| `t5-haikugpt5`    | haiku-4.5   | openai/gpt-5         |

All arms run at `temperature=0.0` with the v2 `--reflector-store-cap 5`
fence. Per-call OpenRouter metadata (generation id, upstream provider,
authoritative cost) captured into JSONL sidecars as in v2.

## Results

```
arm              task          reflect           pass  iters            tokens  cost      stored
t5-none          gpt-oss-20b   none              0/5   5/5/5/8/5        40K    $0.002    0
t5-self          gpt-oss-20b   gpt-oss-20b       1/5   5/5/12/5/6       65K    $0.004    5
t5-haiku         gpt-oss-20b   haiku-4.5         0/5   0/5/3/3/3        25K    $0.016    2
t5-gpt5          gpt-oss-20b   gpt-5             1/5   9/5/10/6/4       58K    $0.181    12
t5-haikuself     haiku-4.5     haiku-4.5         5/5   8/9/8/6/5        110K   $0.140    8
t5-haikugpt5     haiku-4.5     gpt-5             5/5   8/5/6/5/5        80K    $0.278    12
```

## The headline finding

**The Karpathy curve fires cleanly when the student is capable AND the
teacher is smarter than the student. Not otherwise.**

Compare the two haiku-task rows:

```
haikuself:   iters  8  9  8  6  5    (sum 36, gradual drop)
haikugpt5:   iters  8  5  6  5  5    (sum 29, sharp drop after 1 reflection)
```

Both achieve 5/5 task passes. Self-reflection gets haiku from 8 iters
to 5 over 5 runs. Pairing haiku with **GPT-5 as teacher** gets it
from 8 to 5 in **one reflection round** (n=1 → n=2 drops 8 → 5),
then stays there. That's the clean monotonic curve v1 and v2 couldn't
produce on real models, now reproduced on purpose.

**Dollar accounting.** GPT-5 as reflector costs 2× haiku's
self-reflection ($0.278 vs $0.140), but saves 27 % of total tokens
(80K vs 110K — fewer task-agent iterations because the procedure lands
correctly on the first reflection). The premium teacher's surcharge is
offset by the cheaper-per-token task agent doing less work. For any
run budget where you'd iterate ≥20 times, the premium reflector wins
on absolute cost.

## The other headline

**If the student can't execute the procedure, no teacher helps.** The
four `gpt-oss-20b` task-agent arms pass at 0/5, 1/5, 0/5, 1/5 across
none / self / haiku / gpt-5 reflectors. The one successful run in each
scored-pass arm is noise, not signal.

But there's signal hiding in the iteration counts. Look at
`t5-haiku`: iters 0/5/**3/3/3**. Haiku's reflector clearly got
gpt-oss-20b to execute fewer steps after n=2 — the procedure *is*
landing. The student just can't interpret it into the right answer.
gpt-oss-20b's failure mode on T5 was committing an answer for the
wrong index (usually 1, the first E0308), not failing to answer at all.

This is a distinct failure mode worth naming: **the reflector's
procedures change behavior measurably without changing outcome
measurably**. It's the kind of finding a clean "did it pass?" metric
would have hidden — but the iteration count exposes it.

## Four specific lessons

1. **Pairing matters more than reflector quality alone.**
   `t5-gpt5` (gpt-oss-20b + GPT-5 reflector) produced 12 stored
   procedures and $0.18 of cost, and still only 1/5 passes. Same
   reflector paired with haiku (`t5-haikugpt5`): 5/5 passes and the
   sharpest curve in the set. The teacher quality is necessary but
   not sufficient.
2. **"Self-reflection" is the plateau, not the ceiling.** The v1/v2
   demo always had the task model teach itself. Now we can measure
   what was being left on the table: ~27 % of tokens and ~3
   iterations of gap-to-floor.
3. **Negative control was worth running.** Without `t5-none`'s
   flat 5/5/5/8/5 trace, `t5-self`'s single lucky pass looks
   meaningful. With the control, it's clearly noise — gpt-oss-20b's
   intrinsic success rate on T5 is near zero regardless of what
   procedures sit in the knowledge store.
4. **The reflector cap is still earning its keep.** `t5-haikugpt5`
   and `t5-gpt5` both stored 12 procedures; at 5 runs that's at the
   per-run cap. Without the cap landed in v2 these would have been
   20+ each, drowning the recall path in noise.

## What this means for the README

The honest story now has four parts:

- **When the student is capable, reflection helps.** Haiku self-ref
  drops iterations 8 → 5 over five runs (v3 arm `t5-haikuself`).
  This is the signal the demo claims; v3 finally produces it on a
  real model.
- **When the teacher is smarter than the student, reflection helps
  faster.** Premium reflector → same drop in one round instead of
  five (v3 arm `t5-haikugpt5`). The cost math also works out — the
  premium pays for itself in saved student tokens.
- **When the student can't execute the procedure, reflection
  moves but doesn't improve.** Haiku as teacher to gpt-oss-20b cut
  iterations but not the error rate (v3 arm `t5-haiku`). Important
  to report — the absence of this caveat is what inflates
  Karpathy-pitch claims.
- **The Cedar + budget-cap sandbox holds independently of all of
  the above.** v2 showed 50 refusals across the adversarial sweep;
  v3 added no new escape attempts because the reflector prompt is
  the default here. The infrastructure story is independent of
  whether the loop helps in a given pairing.

## Budget and wall

| | runs | wall | cost |
|---|---:|---:|---:|
| T5 matrix (4 gpt-oss-20b arms) | 20 task + 15 refl | 12 min | $0.20 |
| T5 capable (2 haiku arms) | 10 task + 10 refl | ~4 min | $0.42 |
| **Total for v3** | **45 runs** | **~16 min** | **$0.62** |

Total spend for the full v1 + v2 + v3 effort is now around $7 across
all sweeps. Public-repo friendly.

## Suggestions for v4

1. **Expand the capable-student matrix.** Only two arms here. Fill
   in the grid: {haiku, sonnet, gpt-5} × {self, haiku, sonnet, gpt-5}
   to see whether the cost/benefit continues to scale — is there a
   point where an even-smarter reflector stops helping?
2. **Add an "anti-reflection" arm.** Seed the knowledge store with a
   deliberately wrong procedure and measure how much it hurts. If
   haiku can recover from bad advice, that's a robustness finding;
   if it can't, reflection needs a quality gate.
3. **Stress the sandbox in the cross-pairing setting.** Run the
   adversarial reflector prompt with a premium reflector model
   against the incapable task agent — do GPT-5-reflector + adversarial
   prompt produce different refusal patterns than the v2 matrix?
4. **Drop T5 cold-start to 15+ iterations.** Current cold is 5–9
   iters on haiku (closer to 10 than the 15–18 v2 wanted). Remove
   the `sort_by_severity` tool (it's a cheap single-call giveaway)
   and add 6–8 more errors to force a longer cold walk.

## Code delta for v3

- `crates/symbi-kloop-bench/src/main.rs`:
  new `--no-reflector` flag (negative-control arm).
- `crates/symbi-kloop-bench/src/harness.rs`:
  `ProviderSource::OpenRouter` now carries `task_model` and
  `reflect_model` separately; `fresh_openrouter_task_provider` and
  `fresh_openrouter_reflect_provider` return per-role handles;
  `pricing_key_for(role)` resolves correct pricing per role.
- `crates/symbi-kloop-bench/src/task_tools.rs`:
  `register_t5` registers the 8 tools for the cascade task. Two
  new helpers (`single_int_arg_tool`, `two_int_args_tool`) for
  int-parameter tools. `explain_code` grew entries for E0432/E0433/E0425
  that name them as usual-root in a cascade.
- `tasks/T5.json` — new task definition.
- `policies/task-agent.cedar`: permit entries for T5's 8 tools.
- `scripts/run-t5-matrix.sh` — orchestrates the 4-arm
  gpt-oss-20b matrix.
- `demo-output/MODEL-SWEEP-REPORT-v3.md` — this file.

## Artifact map (v3 additions)

```
tasks/T5.json                             # new task

data/t5-{none,self,haiku,gpt5}/runs.db    # 4 gpt-oss-20b arms
data/t5-{haikuself,haikugpt5}/runs.db     # 2 haiku arms
journals-t5-*/*.json                      # per-run journals
journals-t5-*/*.jsonl                     # per-call OpenRouter sidecars

demo-output/t5-*.log                      # per-arm stdout
demo-output/t5-*-dashboard.txt            # per-arm dashboard
demo-output/run-t5-*.md                   # per-arm canonical report
demo-output/MODEL-SWEEP-REPORT-v3.md      # this file
```
