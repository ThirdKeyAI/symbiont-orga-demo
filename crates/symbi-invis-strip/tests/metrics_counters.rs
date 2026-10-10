//! Keep process-wide counter assertions in a dedicated integration-test binary
//! so parallel sanitizer tests cannot contribute to the same counters.

#![cfg(feature = "metrics")]

use symbi_invis_strip::{metrics, sanitize_field, sanitize_field_with_markup};

#[test]
fn metrics_counters_tick_on_every_public_call() {
    metrics::reset();
    let _ = sanitize_field("a\u{200B}b");
    let _ = sanitize_field_with_markup("ok<!-- bad -->done");
    let s = metrics::snapshot();
    assert_eq!(s.calls, 2, "expected exactly 2 recorded calls");
    // First call: 5 bytes in (a + 3 for U+200B + b), 2 bytes out.
    // Second call: 18 bytes in, 6 bytes out.
    assert_eq!(s.bytes_in, 23, "expected bytes from both calls");
    assert_eq!(
        s.bytes_stripped, 15,
        "expected removed bytes from both calls"
    );
    assert!(s.ns_total > 0, "ns_total should be non-zero");
}
