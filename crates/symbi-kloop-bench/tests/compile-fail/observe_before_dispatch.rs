//! Prove #3: `observe_results` is not callable on `PolicyCheck`.
//!
//! An attacker-controlled loop driver trying to "record an observation"
//! without dispatching the approved tool call (to, say, fabricate a
//! success for the journal) would need to call `observe_results` on
//! something other than `AgentLoop<Observing>`. The type system
//! forbids that: `observe_results` is only implemented for the
//! `Observing` phase, which you can only reach by calling
//! `dispatch_tools` on `ToolDispatching`.

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, PolicyCheck, Reasoning};
use symbi_runtime::types::AgentId;

fn main() {
    let state = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let config = LoopConfig::default();
    let reasoning = AgentLoop::<Reasoning>::new(state, config);

    // Cast that should itself be impossible — `AgentLoop<Reasoning>`
    // and `AgentLoop<PolicyCheck>` are distinct types — but even if a
    // devious caller fabricated a `PolicyCheck` handle, the line below
    // would still fail to type-check.
    let _ = reasoning;
    let _fake: AgentLoop<PolicyCheck> = panic!("cannot be constructed by outside code");

    // ILLEGAL: `observe_results` is only defined on `AgentLoop<Observing>`.
    // Calling it on `PolicyCheck` must be a method-not-found error.
    let _ = _fake.observe_results();
}
