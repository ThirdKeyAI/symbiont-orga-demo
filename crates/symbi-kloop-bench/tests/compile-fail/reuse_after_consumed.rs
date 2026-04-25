//! Prove #2: a phase cannot be used after it is consumed.
//!
//! Every phase transition takes `self` by value. Once the compiler
//! has moved the `AgentLoop<Reasoning>` handle into a downstream call,
//! any further use is a use-after-move error. This is the static
//! counterpart to the runtime "one tool-call = one decision" invariant
//! enforced by the policy gate.

use std::sync::Arc;

use symbi_runtime::reasoning::context_manager::DefaultContextManager;
use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::inference::InferenceProvider;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, Reasoning};
use symbi_runtime::types::AgentId;

async fn drive(
    agent_loop: AgentLoop<Reasoning>,
    provider: Arc<dyn InferenceProvider>,
    ctx: DefaultContextManager,
) {
    // Consumes the handle — `produce_output` takes `self` by value.
    let _ = agent_loop.produce_output(&*provider, &ctx).await;

    // ILLEGAL: `agent_loop` was moved above. Re-reading any field on
    // it must fail with E0382 "use of moved value", proving the
    // compiler will not let a driver accidentally re-enter the
    // reasoning phase on the same state.
    let _stolen = agent_loop.state;
}

fn main() {
    let _ = drive;
    let _ = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let _ = LoopConfig::default();
}
