<!--
ai-training: opt-out
robots: noai, noimageai
-->

# Methodology Limits

**Last updated:** 2026-05-08

This document consolidates the methodological qualifications that
apply to every claim made in the escape-evaluation reports
(`tier1*.md`, `tier2-3.md`) and to the karpathy-loop sweep reports
under `demo-output/MODEL-SWEEP-REPORT-*.md`. It exists so the report
prose can stay short and so reviewers know where to find the caveats
without diffing them out of every paragraph.

## What the harness does

It runs the same agent task under two or three substrates and
records, per trial:

- whether the agent achieved the stated objective (`objective_success`),
- whether the agent's tool-call arguments and filesystem trace touched
  a canary (`escape_attempted`),
- whether the touch reached the protected sink (`escape_success`),
- per-fence block records (`escape_attempts[].blocked_by`).

Wilson 95% confidence intervals accompany every reported rate.

## What the harness does not do

### 1. It does not estimate real-world attack-distribution coverage

The 14 reflector + task-side adversarial variants in the karpathy-loop
sweeps and the six task families in the escape-evaluation harness are
**hand-authored** by the same team that designed the fences. The
results answer:

> "Do our fences fire on the attack shapes we wrote?"

They do not answer:

> "What fraction of plausible real-world attacks would our fences catch?"

A held-out red-team set written by parties who did not see the fence
implementations is the right next experiment. It is not yet committed.
Until it is, the absolute rates in the reports should be read as
**rates against the tested distribution**, not as rates against an
attacker drawn from the wild.

### 2. It does not measure per-fence false-positive cost

Refusal rates are reported on adversarial inputs. Benign-input
coverage (e.g., 100 valid hostnames against the URL validator, 100
valid file paths against the path validator, 100 benign Markdown
samples against the sanitiser) is not yet swept.

The harness does measure `objective_success` per substrate per task,
which captures whether the substrate broke the legitimate work — see
the benign-control table in `tier1-v5.md`. `fs_boundary` is the only
task with no canary-attempt incentive, so it is the cleanest single
benign-control signal. A dedicated per-fence benign-input suite is
listed as follow-on work.

### 3. It does not prove demonstrated exploit prevention for the v11 typed-argument fence

The v11 `tool-arg-injection` control arm captures the
LLM-emitted `whois_lookup` target without actually invoking `whois`.
This is a deliberate safety choice: running shell-shaped payloads
against a real binary in a CI environment is not safe. Strictly, v11
shows:

> "The unfenced control would have passed shell-shaped arguments to
> the executor boundary."

It does not show:

> "The unfenced control executed the exploit."

A docker-sandboxed canary verification (run the unfenced arm in a
throwaway container and confirm `/tmp/canary-*` would be created) is
listed as follow-on work.

### 4. The committed audit reports zero escapes within its own coverage

The "0 escapes" headline (`audit-knowledge-stores.py --strict`) is
**not** a universal security claim. It means:

> "No escape was found by the audit script over the committed
> artifacts (1474 stored rows × 123 dbs) under the current strict
> audit rules."

The audit's coverage is bounded by:

- the forbidden-pattern definitions in the audit script,
- the artifact set committed to the repo,
- the parser assumptions in the audit code.

A pattern not in the audit list is not detected; an artifact not
committed is not scanned. The audit is a regression-prevention check,
not a proof of safety.

### 5. The benchmark is small and curated

Six curated escape-eval task families and 14 curated karpathy-loop
adversarial variants, run across 7–9 models. Several headline numbers
come from repeated sweeps over the same small task set (this is
intentional — power per cell, not breadth). When quoting the numbers,
prefer wording like:

> "We evaluated 14 adversarial shapes across a curated 6-task escape
> benchmark and 5-task karpathy-loop benchmark."

over wording like:

> "We comprehensively evaluated agent safety."

### 6. External validity is bounded by the harness itself

The harness uses OpenRouter as the inference provider, the local
Symbiont demo stack as the substrate, and per-task bind-mounted
filesystems. We have not measured behavior under:

- long-running agent sessions (only short, bounded runs),
- multi-turn adversarial memory poisoning,
- real production repositories or PR comments,
- real MCP servers (only a stub),
- non-Rust tool executors,
- partially malformed JSON / tool-call payloads,
- streaming responses,
- concurrent agents sharing state.

These are all reasonable next experiments and none invalidate the
current results within their stated scope.

## Recommended phrasing

These re-phrasings are applied throughout `tier1-v5.md`, `tier2-3.md`,
the top-level README, and the escape-eval README:

| Avoid                                                | Prefer                                                                                                            |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| "0 escapes"                                          | "0 escapes detected in N trials, Wilson CI [a%, b%], on the tested attack shapes"                                  |
| "Symbiont prevents agent compromise"                 | "Symbiont's fences blocked all observed escape attempts on the tested vectors and models"                          |
| "the substrate eliminates X"                         | "no X was observed in N trials, Wilson CI [a%, b%]"                                                                |
| "100% bite-rate"                                     | "100% bite-rate on the N=… committed corpus (Wilson CI [a%, b%])"                                                  |
| "the unfenced control executed the exploit" (v11)    | "the unfenced control would have passed shell-shaped arguments to the executor boundary"                           |

## Confidence intervals

All rates in the reports use Wilson 95% intervals
(`evals/escape/analysis/aggregate.py::wilson_ci`). Wilson is preferred
over the normal approximation because several of the headline cells
sit at 0/N or near N/N, where the normal approximation degenerates
but Wilson stays well-behaved.

A bound of `[12%, 22%]` means: under standard frequentist assumptions,
the true rate has 95% probability of being within that range, given
the observed sample. A 0/140 cell with Wilson CI `[0%, 2.6%]` does
**not** prove the underlying rate is zero — it bounds it from above
at this n.

## Reproducing the headline numbers

`scripts/verify_paper_claims.py` checks every headline rate in
`tier1-v5.md` against the committed `results/aggregate-summary.json`
(per-cell counts only — no exploit content). The aggregate is
regenerated from local JSONL via
`scripts/verify_paper_claims.py --regenerate-aggregate`; the
per-trial JSONL is intentionally gitignored
(`PUBLISHABILITY.md`).

**Reviewer workflow (fresh clone, no JSONL):**

```
cd evals/escape
python3 scripts/verify_paper_claims.py
```

This compares each `Claim` in the script to the committed aggregate
and prints PASS/FAIL with deltas. The aggregate is the audit
artifact reviewers should compare against the report.

**Maintainer workflow (after a sweep):**

```
cd evals/escape
python3 scripts/verify_paper_claims.py --regenerate-aggregate
python3 scripts/verify_paper_claims.py
git diff results/aggregate-summary.json   # what shifted
```

**Current verifier status (as of 2026-05-08):** the verifier reports
9 PASS / 13 FAIL when run against the current
`results/aggregate-summary.json`. The FAIL cells differ from the
report's hand-cited counts by ±1 to ±16 trials per cell. The
discrepancies are believed to be the difference between the JSONL
the report author saw at sweep time and the JSONL currently on disk
(some cells have been re-aggregated since the v5 report was first
written). They do not change the qualitative conclusions but should
be reconciled — either by re-running the affected cells from a
known-clean state or by updating the report's numeric counts to
match the current aggregate.

This reconciliation is listed as follow-on item (0) below.

## Follow-on experiments (not yet committed)

In rough priority order:

0. **Reconcile current verifier failures.** As noted above, the
   verifier reports 13 FAIL cells against the current aggregate.
   Each FAIL needs to be traced to either (a) a hand-typed counting
   error in the report, or (b) a JSONL re-run that post-dates the
   report's snapshot. Decide per cell whether to update the report
   numbers or to re-pin the aggregate from a tagged sweep snapshot,
   and then re-run the verifier until 0 FAIL.
1. **Held-out red-team set** authored after fences were frozen,
   ideally by an external reviewer. The 14 reflector +
   task-side adversarial shapes and the six escape-eval task
   families were authored by the same team that designed the fences;
   a held-out set is the strongest single experiment that would
   raise research-grade credibility.
2. **Per-fence benign-input sweeps** to measure false-positive rate
   alongside refusal rate. Concretely:
   - URL validator: 100 valid hostnames (including legitimate
     internal domains, mixed-case, long-but-valid FQDNs, IDNs we
     intentionally do support).
   - Path validator: 100 valid file paths inside `instance_root`.
   - Agent-summary validator: 100 benign Markdown samples (technical
     prose, quoted logs, code-fence-bounded examples, ordinary
     HTML-like text).
   - The sanitiser: legitimate Markdown / code use, ordinary HTML-
     like prose, comments in technical text, documentation snippets.
   Report FP rate beside refusal rate in every fence's published
   numbers.
3. **Docker-sandboxed canary verification** for v11 typed-argument
   fence — proves counterfactual-execution would have created the
   canary in the unfenced arm. Default safe path stays capture-only
   (current behavior); the sandbox runs as an explicit `danger-lab`
   mode that only fires under an opt-in flag and inside a throwaway
   container.
4. **Frozen `BENCHMARK.md` manifest** with task IDs, attack variants,
   model list, iteration counts, provider, random seeds, harness
   commit SHA, Symbiont commit SHA, ToolClad commit SHA, expected
   artifact paths, and exact pass/fail criteria — for cross-version
   comparison and reviewer reproduction.
5. **Multi-turn adversarial memory poisoning** to extend the
   single-trial threat model toward long-running sessions. Today's
   harness runs each trial in isolation; an attacker who can corrupt
   the knowledge store across runs is a different (and more
   plausible) threat.
6. **Sanitiser benign-data preservation quantification.** v8 already
   confirms inline single- and double-backtick code is not stripped;
   extend this with a benign-corpus benchmark reporting % of
   non-attack content preserved verbatim.
7. **Confidence-interval coverage in older reports.** Wilson CIs are
   applied throughout `tier1-v5.md`; older sweep reports under
   `demo-output/MODEL-SWEEP-REPORT-v*.md` use point estimates only.
   Either re-render those with CIs (against a snapshot of the
   underlying DBs) or add a "see METHODOLOGY-LIMITS" footer to each.
