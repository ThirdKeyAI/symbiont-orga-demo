//! Prove #1: you cannot call `dispatch_tools` on a `Reasoning` phase.
//!
//! The typestate pattern says: `Reasoning` only exposes `produce_output`,
//! which consumes `self` and returns `AgentLoop<PolicyCheck>`. The only
//! method available on `PolicyCheck` is `check_policy`, which returns
//! `AgentLoop<ToolDispatching>`. Therefore, attempting to dispatch a
//! tool straight out of the reasoning phase — which is what a naïve
//! loop driver might do if the policy gate is "just a runtime check" —
//! must fail at the type level, not at runtime.
//!
//! The diff between "compile error" and "cedar_denied counter goes up"
//! is the whole point: the compile error proves the defense exists even
//! on inputs we never tested.

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, Reasoning};
use symbi_runtime::types::AgentId;

fn main() {
    let state = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let config = LoopConfig::default();
    let agent_loop = AgentLoop::<Reasoning>::new(state, config);

    // ILLEGAL: `dispatch_tools` is only implemented for
    // `AgentLoop<ToolDispatching>`. Calling it on `Reasoning` must be a
    // type error — the policy gate is *unskippable* in the signature.
    let _ = agent_loop.dispatch_tools(
        panic!("unreachable — this line must not type-check"),
        panic!("unreachable — this line must not type-check"),
    );
}
