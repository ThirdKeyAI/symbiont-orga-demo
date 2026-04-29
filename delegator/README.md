# `delegator/` — the third principal (v6+, fully active in v8)

The delegator runs *before* each iteration and decides which task
should run next. It introduces a third Cedar principal so the
demo can ship the canonical three-principal flow (delegator
picks → task agent solves → reflector teaches) end-to-end.

## Files

| File | Purpose |
|---|---|
| `delegator.symbi` | Symbiont DSL declaration of the delegator's profile. The delegator's only permitted tool is `pick_task`. It cannot probe, store, answer, or shell out. |

The matching Cedar policy lives at `policies/delegator.cedar`.
Same dual-fence design as the reflector: DSL says
"profile-of-one", Cedar enforces it.

## Activation

The delegator is opt-in via `--with-delegator` on the
`symbi-kloop-bench demo` subcommand. When set, every iteration
starts with a delegator pass; the chosen task is the only one
that runs that iteration. When unset, the harness iterates every
loaded task in sorted order (the v1–v5 default).

The mock provider does **not** ship with a delegator script, so
`--with-delegator --provider mock` produces zero task runs by
design. Use `--provider openrouter` (or any cloud provider) to
exercise the three-principal flow.

## Why a third principal?

A two-principal design (task + reflector) is enough to demonstrate
the safety claim; a three-principal design is enough to
demonstrate that the safety claim *composes*. The delegator's
Cedar policy sits independently of the reflector's; an exploit
that broke the reflector's profile-of-one would still need to
separately defeat the delegator's. The v8 report walks through
this end-to-end.
