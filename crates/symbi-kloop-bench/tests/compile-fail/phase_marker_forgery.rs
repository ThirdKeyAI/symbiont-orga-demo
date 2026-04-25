//! Prove #5: user code outside `symbi-runtime` cannot forge a phase
//! marker and route around the transition functions.
//!
//! The phase data carrier is `phase_data: Option<PhaseData>`, a
//! private enum. An outside caller cannot construct `AgentLoop<Observing>`
//! directly because the only public constructors are `AgentLoop::<Reasoning>::new`
//! and the `Ok(AgentLoop { ... })` literals inside the transition
//! methods, which rely on the private enum. This file verifies that
//! attempting the obvious escape hatch — building `AgentLoop<Observing>`
//! via a struct literal — is a compile error because of private fields.

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, Observing};
use symbi_runtime::types::AgentId;

fn main() {
    let state = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let config = LoopConfig::default();

    // ILLEGAL: `AgentLoop` has private fields (`phase_data`, `_phase`).
    // A struct literal from outside the defining module cannot fill them,
    // so forging an `Observing` phase to call `observe_results` fails
    // at the type / privacy level.
    let _ = AgentLoop::<Observing> {
        state,
        config,
    };
}
