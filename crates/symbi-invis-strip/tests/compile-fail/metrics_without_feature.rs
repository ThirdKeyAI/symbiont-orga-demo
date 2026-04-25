//! Without `--features metrics`, the `metrics` module does not
//! exist (it's gated behind `#[cfg(feature = "metrics")]` in
//! `lib.rs`). Calling into it must fail with E0433 "use of
//! unresolved module".

fn main() {
    let _ = symbi_invis_strip::metrics::snapshot();
}
