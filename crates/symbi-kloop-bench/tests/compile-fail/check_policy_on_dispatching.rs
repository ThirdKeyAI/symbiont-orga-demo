//! Prove #8 (v10): `check_policy` is unreachable from
//! `AgentLoop<ToolDispatching>`. Catches a refactor that exposes
//! "re-run the policy check on the dispatch phase for paranoia"
//! — sounds defensive, would actually let a buggy driver double-
//! dispatch one set of approved actions then recheck the next
//! batch, leaking the in-progress dispatch state through a
//! second policy evaluation.
//!
//! Combined with v9's `skip_policy_check.rs` (which proved
//! `dispatch_tools` is unreachable from Reasoning), this pins
//! the directionality: forward transitions only, no backwards
//! recheck path.

use std::str::FromStr;

use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::loop_types::{LoopConfig, LoopState};
use symbi_runtime::reasoning::phases::{AgentLoop, ToolDispatching};
use symbi_runtime::types::AgentId;

fn main() {
    // We can't construct AgentLoop<ToolDispatching> from outside
    // the module (private fields — see phase_marker_forgery.rs in
    // v9), so this file constructs a `panic!()` placeholder to
    // satisfy the type checker. The compile error must come from
    // method resolution on AgentLoop<ToolDispatching>, NOT from
    // the construction step.
    let dispatching: AgentLoop<ToolDispatching> =
        panic!("placeholder; the compile error must precede this line");

    // ILLEGAL: `check_policy` is only impl'd for
    // AgentLoop<PolicyCheck>. Calling it on ToolDispatching is a
    // method-not-found error; the type system refuses the
    // double-policy-check shape.
    let _ = dispatching.check_policy(panic!("gate"));
    let _ = LoopConfig::default();
    let _ = LoopState::new(AgentId::new(), Conversation::with_system("x"));
    let _ = String::from_str("noop").unwrap();
}
