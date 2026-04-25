//! Prove #4: `ReasoningLoopRunner::builder().build()` is a type error
//! unless both `provider` and `executor` are set.
//!
//! The builder carries its "required field" state as type parameters:
//! `ReasoningLoopRunnerBuilder<(), ()>`. `provider(…)` flips the first
//! slot to `Arc<dyn InferenceProvider>`; `executor(…)` flips the second
//! to `Arc<dyn ActionExecutor>`. `build()` is only implemented for the
//! fully-set combination. So forgetting the provider is a
//! method-not-found at compile time — not a runtime `None` panic.

use symbi_runtime::reasoning::reasoning_loop::ReasoningLoopRunner;

fn main() {
    // ILLEGAL: `build()` is only implemented for the fully-filled
    // builder. Calling it on the empty builder must fail at the type
    // level, proving that no operator can ever spin up a loop with a
    // missing provider or executor.
    let _ = ReasoningLoopRunner::builder().build();
}
