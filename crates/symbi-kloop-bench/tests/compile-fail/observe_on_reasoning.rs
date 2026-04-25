//! Prove #7 (v10): `observe_results` is unreachable from the
//! Reasoning phase, not just from PolicyCheck (which v9 already
//! covered). The full attack surface is "every phase from which
//! the call is supposed to be impossible", not just one. This
//! file pins the lower-bound case: a fresh handle in the
//! Reasoning phase, called immediately, fails with the same
//! method-not-found error that the PolicyCheck case produced
//! in v9.
//!
//! Together with `observe_before_dispatch.rs`, this gives the
//! paper a 2/4 phase-coverage guarantee for `observe_results`:
//! Reasoning → No, PolicyCheck → No. The other two phases
//! (ToolDispatching, Observing) are excluded because
//! ToolDispatching's only outbound method *is* `observe_results`
//! and Observing trivially permits it.

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, Reasoning};
use symbi_runtime::types::AgentId;

fn main() {
    let state = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let config = LoopConfig::default();
    let agent_loop = AgentLoop::<Reasoning>::new(state, config);

    // ILLEGAL: `observe_results` is only implemented for
    // AgentLoop<Observing>. From Reasoning the only available
    // method is `produce_output`. Calling observe_results here
    // must be a method-not-found error.
    let _ = agent_loop.observe_results();
}
