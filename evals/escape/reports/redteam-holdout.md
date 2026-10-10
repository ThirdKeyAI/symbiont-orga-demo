<!--
ai-training: opt-out
robots: noai, noimageai
-->

# Held-out red-team: injection generalisation + the structural fix

**Date:** 2026-06-03
**Scope:** the orchestrator-injection vector, attacked with a blind-authored
held-out set and scored behaviorally. Addresses
[`METHODOLOGY-LIMITS.md`](../METHODOLOGY-LIMITS.md) item 1 (no held-out
attack distribution).

**All numbers measure rates against the tested attack distribution and
model set.** Reproduce with `redteam/run_holdout.py` + `redteam/analyze.py`
(per-trial JSONL is gitignored per [`PUBLISHABILITY.md`](../PUBLISHABILITY.md);
aggregate-gating of these numbers in `scripts/verify_paper_claims.py` is
follow-on).

## Why this experiment

Every attack shape in the prior reports — the six task families, the
injection lures, the canary patterns — was authored by the same team that
designed the fences. So those rates answer *"do our fences fire on the
attack shapes we wrote?"*, not *"what fraction of plausible real-world
attacks would they catch?"*. This set was authored **blind**: a subagent
with **zero access** to the validators, marker list, or canary patterns
wrote 90 attack lures (30 each for injection, fs_enumerate, net_boundary)
from a threat-model-only brief ([`redteam/BRIEF.md`](../redteam/BRIEF.md)).
It approximates a held-out red team in-repo; it is weaker than an external
human author (documented limitation below).

It also changes the **scoring** for injection. The in-distribution metric
is a regex canary over the worker's `answer` text — and that regex shares
its vocabulary with the marker list it is measuring, so it is partly
circular (the fence blocks marker-shaped text; a marker-shaped detector
then sees few "successes"). Here injection is scored **behaviorally**: the
downstream supervisor is actually run on the worker's output, and we check
whether routing flips to `exec_escalation`.

## Headline: the content marker fence does not generalise

Injection, behavioral scoring, pooled across 4 frontier models
(claude-sonnet-4.6, gpt-5, deepseek-v4-pro, qwen3.6-max-preview), n=120/arm:

| arm | escape (Wilson 95%) |
|---|---|
| unguarded baseline (free-text → LLM supervisor) | **28.3%** [21%, 37%] |
| symbiont `agent_summary` marker fence | **25.8%** [19%, 34%] |

On blind-authored attacks scored behaviorally, the marker fence provides
**no statistically significant protection** — the CIs overlap heavily.
Contrast the in-distribution v5 number (3.6% vs 59%): that gap was inflated
by (a) attacks shaped like the marker list and (b) the marker-aligned regex
canary. The marker fence blocked 10/120 answers, but models paraphrased
past it (4 confirmed paraphrase-past escapes). Per model, excluding sonnet
(which *refuses* the lure 18/30 via alignment, not the fence): deepseek-pro
9→9, gpt-5 13→12, qwen-max 12→10 — the fence barely moves escape.

This is consistent with the v5 report's own "regex ceiling" caveat for
GPT-5; the held-out set shows it generalises beyond GPT-5 to novel attacks
across models.

## The fix: typed channel + grounded decision (by construction)

The vector is a confused-deputy problem: untrusted worker free-text is
spliced into a higher-privilege position (the supervisor's instructions).
Content filtering that surface — markers *or* an LLM judge — fights
unbounded paraphrase. The structural fix removes the surface: make the
privileged decision over **typed enum fields**, never free text, and
**ground** it in trusted context.

Same held-out attacks, same models, n=120/arm. The worker submits a typed
`submit_triage(category: enum, severity: enum, rationale)`; routing is a
deterministic policy:

| arm | escape |
|---|---|
| typed channel, **trusting** router (trusts worker's `severity`) | **38–40%** |
| typed channel, **grounded** router (caps `severity` by the trusted ticket) | **0/120** [0%, 3%] |

Two lessons:
- **Typing alone is not security — it can be worse.** A router that trusts
  the worker's self-reported `severity` is *40%*: the attack no longer needs
  to paraphrase, it just sets `severity=critical` (46/120 workers did).
  deepseek/gpt-5 went *up* vs the marker fence.
- **The security comes from grounding the decision in trusted input.** Cap
  the worker's claim by a severity derived from the *trusted ticket* → a
  compromised worker cannot escalate a benign ticket → 0%.

## Validated on the shipping runtime

The fix is not only a prototype. The Symbiont runtime ships the typed +
grounded decision (`symbi_runtime::toolclad::decision`: a typed enum
channel, `ticket_severity` grounding, and a Cedar escalation policy that
gates on the *trusted* ticket severity, not the worker's claim). Driving the
symbiont substrate's decision through that shipping module:

- **0/120** escalations [0%, 3%], **0/30 on every model**.
- Attack confirmed live: 46/120 workers self-reported `severity=critical`.
- The shipping runtime's routing decision **matched the validated prototype
  on 120/120 trials** — the 0% is the runtime's own decisions, not a scorer
  artifact.

The `agent_summary` marker fence is unchanged behaviorally (still rejects
markers, now also emits telemetry) and is repositioned as defense-in-depth;
the marker-arm 26% therefore stands.

## Source-grounded claims note

The shipping injection control before this work was `agent_summary` —
`detect_injection_patterns`, a 97-entry literal-substring marker blocklist
(`symbi-invis-strip`). That is **detection**, and the held-out data shows it
does not generalise. The fix uses Symbiont's existing **`enum`** validator
(real typed-argument enforcement, as for ports/paths/URLs/IPs) plus Cedar
grounding — i.e. *by construction*, not detection. The honest claim
boundaries:

- ✅ Symbiont enforces typed, capability-constrained tool arguments and now
  a typed + grounded inter-agent decision — held-out injection 0/120 on the
  shipping runtime.
- ❌ "A marker/guardrail fence blocks injection" — it does not (26% ≈ 28%).
- The pure-action vectors (fs/net/syscall/state) block structurally (no tool
  exposed) and are not subject to the paraphrase ceiling — a different
  mechanism from the injection fix.

## Scope and what this does NOT show

- **Benign tickets only.** 0% holds because the held-out tickets are benign
  (trusted `ticket_severity` resolves low). An attacker who controls the
  **ticket text** (indirect injection) is a separate surface; `ticket_severity`
  is a transparent, bypassable keyword rule against trusted input.
- **Blind subagent ≠ external human.** The corpus author has general attack
  knowledge but hasn't seen *these* fences; an external human set against a
  frozen fence commit is the stronger next experiment.
- **Scoring differs across arms.** The marker/baseline arms use an LLM
  supervisor; the typed arms use a deterministic policy. The clean
  apples-to-apples comparison is *within* the typed arm: trusting 38–40% →
  grounded 0%.
- **fs/net held-out vectors — now runnable via the confined baseline.**
  Previously deferred because the *permissive* baseline arm ran the worker
  against the real host filesystem/network (it crawled a real backup drive
  during a pilot). The `python_confined` substrate (Docker, `--network none`,
  instance-root-only mounts, LLM reached over a bind-mounted unix socket) is
  the contained no-fence baseline that makes them safe to run:

  ```
  python3 -m redteam.run_holdout --vectors fs_enumerate,net_boundary \
      --substrates python_confined,symbiont --execute
  ```

  Containment is verified (all attack `connect()`s return `ENETUNREACH`;
  fs reads resolve inside the box). Note one fidelity caveat for net: under
  `--network none` DNS does not resolve, so *hostname* targets never reach a
  real IP — the authoritative net signal in the confined arm is therefore
  "the substrate **allowed** the forbidden `http_get`" (recorded as an
  unblocked attempt), vs symbiont blocking it at `scope_target`. IP-literal
  targets (e.g. metadata `169.254.169.254`) still fire the connect()-canary
  directly. fs uses the normal relocated-glob `escape_success`.

## Reproduce

```
cd evals/escape
# marker arm (existing fences) + behavioral supervisor:
python3 -m redteam.run_holdout --mode marker  --substrates python_baseline,symbiont --execute
# typed-channel fix, shipping runtime decision:
python3 -m redteam.run_holdout --mode typed   --substrates symbiont --execute
python3 -m redteam.analyze
```
Requires `OPENROUTER_API_KEY` and the symbiont runtime with the
`toolclad::decision` module (the typed+grounded inter-agent branch).
