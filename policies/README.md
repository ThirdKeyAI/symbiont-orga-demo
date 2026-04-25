# `policies/` — Cedar policies, one per principal

Every tool dispatch in the loop is gated by a Cedar policy
keyed on a stable principal label (`Agent::"task_agent"`,
`Agent::"reflector"`, `Agent::"delegator"`). Each `.cedar` file
is the source-of-record for which actions a principal is
permitted to perform. The runtime has no permissive default;
anything not explicitly permitted is denied.

## Files

| File | Principal | Permits |
|---|---|---|
| `task-agent.cedar` | `Agent::"task_agent"` | task-domain probing tools (`pod_status`, `error_detail`, ...) plus `answer`. **No** `store_knowledge`, **no** `system_shell`. |
| `reflector.cedar` | `Agent::"reflector"` | exactly one tool: `store_knowledge`. Profile-of-one. |
| `delegator.cedar` | `Agent::"delegator"` | exactly one tool: `pick_task`. Profile-of-one. |

## Linting

`scripts/lint-cedar-policies.py` runs in CI on every push and PR
(`safety-gates` job in `.github/workflows/ci.yml`). It refuses
any policy file that contains:

- **Homoglyph identifiers** (Cyrillic/Greek letters posing as
  ASCII in `Action::"…"` literals — would let an attacker bypass
  a `forbid` rule by spelling its action name with look-alikes).
- **Invisible control characters** (any code point in the
  sanitiser's forbidden ranges — would let an attacker hide a
  zero-width-space inside an action name and bypass exact
  match).

These two attack shapes are how a malicious PR could weaken the
policy without touching anything obvious in a code review. The
linter catches them pre-merge.

## How a tool call reaches Cedar

```
LLM tool_call → loop_driver → AgentLoop<PolicyCheck>::check_policy(gate)
                                  ↓
                            cedar_policy::Authorizer::is_authorized(req, policies)
                                  ↓
                            LoopDecision::{Allow,Deny,Modify}
```

The gate is invoked between the `Reasoning` and `ToolDispatching`
phases of the typestate machine. **Skipping the gate is
compile-time impossible** — `AgentLoop<Reasoning>` has no
`dispatch_tools` method; the only way out of the reasoning phase
is `produce_output(...)` which returns `AgentLoop<PolicyCheck>`.
See `crates/symbi-kloop-bench/tests/compile-fail/` for the five
trybuild proofs.

## Updating a policy

1. Edit the relevant `.cedar` file.
2. Run `scripts/lint-cedar-policies.py` locally.
3. Run the policy-gate unit tests:
   `cargo test --release -p symbi-kloop-bench policy_gate`.
4. Re-run an adversarial sweep (`VARIANT=adversarial scripts/run-openrouter-sweep.sh 10 ONLY=haiku45`)
   and confirm `cedar_denied` matches expectations.
