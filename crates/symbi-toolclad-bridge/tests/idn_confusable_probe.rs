//! Extended confusable / encoding stress probe for ToolClad's `scope_target`.
//!
//! Follow-up to `idn_probe.rs`. The v11 finding was that v0.5.3 rejected a
//! Cyrillic homoglyph but accepted its punycode form; v0.6.0 closed that. This
//! probe throws the *whole class* at the validator to look for remaining gaps:
//! mixed-script confusables, case/position variants of punycode, NFKC
//! compatibility chars, and invisible/control characters.
//!
//! Empirical, like idn_probe — it prints every outcome. It additionally asserts
//! that every case marked `should_refuse` was REFUSED, so a real bypass fails
//! CI loudly. Cases that are legitimate ASCII (e.g. digit/`rn` typosquats) are
//! marked NOT should_refuse: a hostname validator cannot reject valid ASCII, and
//! that threat (ASCII look-alikes) is out of scope for this fence by design.

use std::path::PathBuf;

use symbi_toolclad_bridge::{validate_args, FenceOutcome, LoadedManifest};

fn manifests_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.pop();
    p.pop();
    p.push("manifests");
    p
}

struct Case {
    group: &'static str,
    label: &'static str,
    value: String,
    /// true => a correct fence MUST refuse this (deceptive / non-ASCII / encoded)
    should_refuse: bool,
}

fn c(group: &'static str, label: &'static str, value: impl Into<String>, should_refuse: bool) -> Case {
    Case { group, label, value: value.into(), should_refuse }
}

fn cases() -> Vec<Case> {
    vec![
        // --- legitimate ASCII: MUST be allowed (control) ---
        c("ascii-legit", "plain", "example.com", false),
        c("ascii-legit", "subdomain", "api.example.com", false),
        // ASCII look-alikes are valid hostnames — out of scope for this fence.
        c("ascii-lookalike(oos)", "digit-one", "examp1e.com", false),
        c("ascii-lookalike(oos)", "rn-for-m", "exarnple.com", false),

        // --- non-ASCII confusables: MUST be refused ---
        c("confusable", "cyrillic-a", "ex\u{0430}mple.com", true),
        c("confusable", "greek-omicron", "g\u{03bf}\u{03bf}gle.com", true),
        c("confusable", "fullwidth-a", "\u{ff45}xample.com", true),
        c("confusable", "math-sans-e", "\u{1d5be}xample.com", true),
        c("confusable", "armenian-o", "g\u{0585}\u{0585}gle.com", true),
        c("confusable", "combining-acute", "exa\u{0301}mple.com", true),
        c("confusable", "latin-sharp-s", "stra\u{00df}e.com", true),
        c("confusable", "greek-final-sigma", "example\u{03c2}.com", true),

        // --- punycode case / position variants: MUST be refused ---
        c("punycode", "lower-leading", "xn--example-9c.com", true),
        c("punycode", "UPPER-leading", "XN--EXAMPLE-9C.COM", true),
        c("punycode", "Mixed-leading", "Xn--example-9c.com", true),
        c("punycode", "xN-leading", "xN--example-9c.com", true),
        c("punycode", "non-leading-label", "www.xn--example-9c.com", true),
        c("punycode", "punycode-subdomain", "xn--80ak6aa92e.example.com", true),
        c("punycode", "trailing-dot", "xn--example-9c.com.", true),
        c("punycode", "UPPER-non-leading", "WWW.XN--EXAMPLE-9C.COM", true),

        // --- invisible / control / whitespace: MUST be refused ---
        c("invisible", "zero-width-space", "examp\u{200b}le.com", true),
        c("invisible", "zero-width-joiner", "examp\u{200d}le.com", true),
        c("invisible", "bom-prefix", "\u{feff}example.com", true),
        c("invisible", "rtl-override", "\u{202e}example.com", true),
        c("invisible", "leading-space", " example.com", true),
        c("invisible", "embedded-tab", "exa\tmple.com", true),
        c("invisible", "trailing-newline", "example.com\n", true),
        c("invisible", "nul-byte", "example.com\u{0000}", true),
    ]
}

#[test]
fn idn_confusable_scope_target_probe() {
    let manifest =
        LoadedManifest::from_path(manifests_dir().join("whois_lookup.clad.toml"))
            .expect("manifest loads");

    let mut gaps: Vec<String> = Vec::new();
    let mut last_group = "";
    println!("\nextended confusable / encoding probe (scope_target):");
    for case in cases() {
        if case.group != last_group {
            println!("  [{}]", case.group);
            last_group = case.group;
        }
        let outcome = validate_args(&manifest, &serde_json::json!({ "target": case.value }))
            .expect("validate_args runs");
        let allowed = matches!(outcome, FenceOutcome::Validated(_));
        let verdict = if allowed { "ALLOWED" } else { "REFUSED" };
        let flag = if case.should_refuse && allowed {
            gaps.push(format!("{}/{} ({:?})", case.group, case.label, case.value));
            "   <<< GAP: should have been REFUSED"
        } else if !case.should_refuse && !allowed {
            "   (note: legit input refused — false positive)"
        } else {
            ""
        };
        println!("    {:22} {:30?} -> {}{}", case.label, case.value, verdict, flag);
    }

    if gaps.is_empty() {
        println!("\nno bypass gaps found across {} cases.", cases().len());
    } else {
        println!("\n{} BYPASS GAP(S) FOUND:", gaps.len());
        for g in &gaps {
            println!("  - {g}");
        }
    }
    assert!(gaps.is_empty(), "scope_target confusable bypass gaps: {gaps:?}");
}

/// Adjacent classes: IP-literal encodings (SSRF-style obfuscation), double-
/// encoded punycode, over-length labels, and URL-shaped inputs.
///
/// Empirical and print-first: scope_target's intended IP policy isn't asserted
/// here (whois legitimately takes IPs), so each case is annotated as `danger`
/// (an ALLOWED result is a bypass surface worth a human look) or not. The hard
/// assertion is limited to cases that MUST be refused on the same rule the
/// confusable probe already proves: any label containing `xn--`.
#[test]
fn scope_target_ip_and_encoding_probe() {
    let manifest =
        LoadedManifest::from_path(manifests_dir().join("whois_lookup.clad.toml"))
            .expect("manifest loads");

    // (group, label, value, danger: ALLOWED would be concerning, must_refuse)
    let long_label = format!("{}.com", "a".repeat(64)); // > 63-char DNS label
    let long_host = format!("{}.com", "a.".repeat(130)); // > 253-char hostname
    let cases: Vec<(&str, &str, String, bool, bool)> = vec![
        ("ip-canonical", "public-dotted-quad", "93.184.216.34".into(), false, false),
        ("ip-canonical", "loopback-dotted", "127.0.0.1".into(), false, false),
        ("ip-obfuscated", "decimal-loopback", "2130706433".into(), true, false),
        ("ip-obfuscated", "hex-loopback", "0x7f000001".into(), true, false),
        ("ip-obfuscated", "octal-loopback", "0177.0.0.1".into(), true, false),
        ("ip-obfuscated", "shorthand-127.1", "127.1".into(), true, false),
        ("ip-obfuscated", "mixed-octal-dec", "0177.0.0.01".into(), true, false),
        ("ip-metadata", "imds-dotted", "169.254.169.254".into(), true, false),
        ("ip-metadata", "imds-decimal", "2852039166".into(), true, false),
        ("ip-metadata", "imds-hex", "0xa9fea9fe".into(), true, false),
        ("ipv6", "loopback", "::1".into(), true, false),
        ("ipv6", "bracketed", "[::1]".into(), true, false),
        ("ipv6", "v4-mapped", "::ffff:127.0.0.1".into(), true, false),
        ("double-punycode", "nested-xn", "xn--xn---abc.com".into(), true, true),
        ("double-punycode", "xn-in-tld", "example.xn--abc".into(), true, true),
        ("length", "label-64", long_label, true, false),
        ("length", "host-260", long_host, true, false),
        ("url-shaped", "scheme-prefix", "http://example.com".into(), true, false),
        ("url-shaped", "port-suffix", "example.com:80".into(), true, false),
        ("url-shaped", "userinfo", "user@example.com".into(), true, false),
        ("url-shaped", "path-suffix", "example.com/admin".into(), true, false),
    ];

    let mut must_refuse_gaps: Vec<String> = Vec::new();
    let mut allowed_danger: Vec<String> = Vec::new();
    let mut last = "";
    println!("\nIP / encoding / length / URL probe (scope_target):");
    for (group, label, value, danger, must_refuse) in &cases {
        if *group != last {
            println!("  [{group}]");
            last = group;
        }
        let outcome = validate_args(&manifest, &serde_json::json!({ "target": value }))
            .expect("validate_args runs");
        let allowed = matches!(outcome, FenceOutcome::Validated(_));
        let mut note = "";
        if *must_refuse && allowed {
            must_refuse_gaps.push(format!("{group}/{label} ({value:?})"));
            note = "   <<< GAP: must refuse (contains xn--)";
        } else if *danger && allowed {
            allowed_danger.push(format!("{group}/{label} ({value:?})"));
            note = "   <- ALLOWED (review: bypass surface?)";
        }
        let verdict = if allowed { "ALLOWED" } else { "REFUSED" };
        println!("    {label:22} {value:34?} -> {verdict}{note}");
    }

    println!("\nsummary: {} of {} 'danger' inputs were ALLOWED.",
             allowed_danger.len(), cases.iter().filter(|c| c.3).count());
    for a in &allowed_danger {
        println!("  review: {a}");
    }
    // Only the xn-- rule is a hard guarantee here; the rest is for human review.
    assert!(must_refuse_gaps.is_empty(),
            "punycode label slipped scope_target: {must_refuse_gaps:?}");
}
