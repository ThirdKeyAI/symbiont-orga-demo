//! Prove #6 (v10): phase-scoped *accessor* methods are also
//! unreachable from the wrong phase. The v9 proofs targeted the
//! *transition* methods (`produce_output`, `check_policy`,
//! `dispatch_tools`, `observe_results`); this proof targets the
//! cheap read-only accessors (`policy_summary`,
//! `observation_count`, `proposed_actions`).
//!
//! The motivation: a refactor that "exposes the policy summary
//! everywhere for convenience" would silently downgrade the
//! typestate from "phase-scoped invariants" to "phase-scoped
//! transitions, but you can read state from any phase". The
//! invariant the paper claims is the stronger version, and this
//! proof pins it.

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, Reasoning};
use symbi_runtime::types::AgentId;

fn main() {
    let state = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let config = LoopConfig::default();
    let agent_loop = AgentLoop::<Reasoning>::new(state, config);

    // ILLEGAL: `policy_summary()` is only implemented for
    // `AgentLoop<ToolDispatching>` — the phase where policy output
    // is the most-recent phase data. Calling it on Reasoning means
    // the phase data slot is None or the wrong variant; the
    // typestate prevents the question from being asked at all.
    let _ = agent_loop.policy_summary();
}
