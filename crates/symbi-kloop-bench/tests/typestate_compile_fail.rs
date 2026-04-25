//! Compile-fail proofs for the `AgentLoop<Phase>` typestate (v9).
//!
//! The 460 Cedar refusals we report in the v8 README are runtime
//! evidence that the *policy gate* is wired in. They do NOT prove the
//! typestate claim — that it is *structurally impossible* to skip a
//! phase — because a runtime counter can only demonstrate behaviour on
//! inputs we happened to feed. The type-level claim is: the compiler
//! itself rejects any source that tries to call `dispatch_tools`
//! without first calling `check_policy`, etc.
//!
//! Each file in `tests/compile-fail/` exercises one illegal transition
//! (reasoning → dispatch, policy → observe, builder without provider,
//! double consumption, phase-data skipping). `trybuild` compiles them
//! with rustc, asserts compilation fails, and diff's the error output
//! against a checked-in `.stderr` snapshot.
//!
//! To refresh the snapshots after a runtime refactor:
//!
//!     TRYBUILD=overwrite cargo test -j2 -p symbi-kloop-bench \
//!         --test typestate_compile_fail
//!
//! Then inspect and commit the regenerated `.stderr` files.
//!
//! These tests do not run on stable Windows CI in the runtime repo,
//! but the runtime crate is path-deped here so the snapshots are
//! pinned to the same Rust version operators use to build the demo.

#[test]
fn typestate_transitions_are_compile_errors() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/compile-fail/*.rs");
}
