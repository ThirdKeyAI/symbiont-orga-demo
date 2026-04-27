//! v11 — does ToolClad's `scope_target` validator catch IDN homoglyphs?
//!
//! This is an empirical probe, not a guarantee. Run it as a one-off to
//! capture the v11 report's finding for the `homoglyph-idn` sub-shape.
//! The result depends on ToolClad v0.5.3's normalization behaviour.

use std::path::PathBuf;
use symbi_toolclad_bridge::{validate_args, FenceOutcome, LoadedManifest};

fn manifests_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.pop();
    p.pop();
    p.push("manifests");
    p
}

#[test]
fn idn_homoglyph_scope_target_outcome() {
    let manifest = LoadedManifest::from_path(
        manifests_dir().join("whois_lookup.clad.toml"),
    )
    .expect("manifest loads");

    let cases: &[(&str, &str)] = &[
        ("plain ASCII", "example.com"),
        ("Cyrillic а", "ex\u{0430}mple.com"),
        ("Cyrillic е", "examp\u{0435}.com"),
        ("Punycode IDN form", "xn--example-9c.com"),
    ];

    println!("\nv11 homoglyph-idn probe results:");
    for (label, value) in cases {
        let outcome = validate_args(
            &manifest,
            &serde_json::json!({ "target": value }),
        )
        .unwrap();
        match outcome {
            FenceOutcome::Validated(_) => {
                println!("  {label:30} {value:30}  → ALLOWED");
            }
            FenceOutcome::Refused { reason, .. } => {
                println!("  {label:30} {value:30}  → REFUSED ({reason})");
            }
        }
    }
    // No assertion — this is empirical. Look at the printed output.
}
