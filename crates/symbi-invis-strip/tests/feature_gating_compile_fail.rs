//! Cross-crate compile-fail proof (v10).
//!
//! The bench crate's `tests/compile-fail/` proves that consumers
//! of the `AgentLoop<Phase>` typestate can't bypass it. This
//! file proves the symmetric claim for the sanitiser crate's
//! feature gating: when built **without** `--features metrics`,
//! the `metrics` module is structurally unreachable.
//!
//! Why bother: the `metrics` module exposes process-wide atomic
//! counters. A future contributor who reaches for them outside a
//! metrics-enabled build would silently get nothing back (the
//! counters wouldn't tick because the records function is
//! `#[cfg(feature = "metrics")]`). We'd rather they get a
//! compile error so the contract is auditable.

#[test]
fn metrics_module_inaccessible_without_feature() {
    let t = trybuild::TestCases::new();
    // The compile-fail file under tests/compile-fail/ tries to
    // call `metrics::snapshot()` unconditionally. When the
    // sanitiser crate is built with --features metrics (which is
    // what the workspace does — see the root Cargo.toml's
    // `[workspace.dependencies] symbi-invis-strip = { ...
    // features = ["metrics"] }`), the module IS accessible and
    // this test must skip. We special-case that.
    if cfg!(feature = "metrics") {
        eprintln!(
            "skipping feature_gating_compile_fail: metrics feature is on \
             at the consumer level (root workspace enables it). Run with \
             `--no-default-features` and an --exclude on the workspace \
             feature unification to exercise this proof."
        );
        return;
    }
    t.compile_fail("tests/compile-fail/*.rs");
}
