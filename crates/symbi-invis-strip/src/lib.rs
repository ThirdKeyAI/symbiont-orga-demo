//! Strip invisible / steganographic Unicode code points from strings.
//!
//! This crate extracts the [`sanitize_field`] helper used by the
//! `symbiont-karpathy-loop` demo's knowledge store, packaged as a
//! standalone zero-dep library for reuse by other agent frameworks
//! that write user-influenced strings into long-term memory.
//!
//! ## Why it exists
//!
//! LLM agents that write to a knowledge store are a ready channel for
//! prompt injection and payload smuggling. An attacker-controlled
//! reflector or a confused planner can emit a seemingly-innocent
//! triple whose `object` field contains zero-width characters, bidi
//! overrides, the Unicode Tag block, ASCII C0 controls, or any of the
//! other canonical steganographic channels. Those payloads survive
//! round-trip into storage and resurface when the next agent reads the
//! store — at which point the knowledge-store content becomes an
//! instruction surface.
//!
//! The fix is straightforward and content-agnostic: drop every
//! code point that has no legitimate textual use, before it lands.
//! That's what [`sanitize_field`] does.
//!
//! ## Forbidden ranges
//!
//! - `U+0000..=U+0008`, `U+000B..=U+000C`, `U+000E..=U+001F`, `U+007F`
//!   — ASCII C0 controls and DEL (excluding `\t`, `\n`, `\r`).
//! - `U+0080..=U+009F` — C1 control block.
//! - `U+200B..=U+200F` — zero-width + LRM/RLM.
//! - `U+202A..=U+202E` — bidi explicit directional overrides.
//! - `U+2060..=U+206F` — word joiner, invisible operators, bidi
//!   isolates, deprecated format controls.
//! - `U+FEFF` — BOM / ZWNBSP.
//! - `U+180E` — Mongolian vowel separator (legacy invisible).
//! - `U+1D173..=U+1D17A` — musical notation invisible format chars.
//! - `U+FE00..=U+FE0F` — variation selectors (emoji-VS steg).
//! - `U+E0100..=U+E01EF` — supplementary variation selectors.
//! - `U+E0000..=U+E007F` — Unicode Tag block (primary steg channel
//!   for "invisible text").
//!
//! Legitimate printable content — ASCII, CJK, Cyrillic, diacritics,
//! emoji proper, regular whitespace (`\t`, `\n`, `\r`) — survives
//! unchanged.
//!
//! ## Minimal example
//!
//! ```
//! use symbi_invis_strip::sanitize_field;
//!
//! // Zero-width space smuggled between two words:
//! assert_eq!(sanitize_field("store\u{200B}knowledge"), "storeknowledge");
//!
//! // Multilingual content roundtrips:
//! assert_eq!(sanitize_field("Привет 世界"), "Привет 世界");
//! ```
//!
//! ## No dependencies, `no_std`-compatible
//!
//! The crate has zero dependencies and uses only `core`-equivalent
//! operations, so it's cheap to drop into any agent framework.
//!
//! ## Sync point
//!
//! The forbidden-range list is also mirrored in two Python scripts in
//! the upstream repo:
//! `scripts/audit-knowledge-stores.py` (post-sweep DB scanner) and
//! `scripts/lint-cedar-policies.py` (Cedar-file linter). Drift between
//! this table and those scripts is a bug; update all three together.

#![forbid(unsafe_code)]

/// Remove every forbidden code point from `s`, returning a sanitised
/// [`String`]. Idempotent: `sanitize_field(sanitize_field(x)) ==
/// sanitize_field(x)`.
///
/// See the [crate-level documentation](crate) for the full list of
/// forbidden ranges and the rationale for each.
pub fn sanitize_field(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if !is_forbidden(c as u32) {
            out.push(c);
        }
    }
    out
}

/// `true` iff `code` is one of the ranges `sanitize_field` strips.
/// Exposed so callers that want to *detect* rather than strip (e.g.
/// audit scripts, linters) can share the same authoritative list.
#[inline]
pub const fn is_forbidden(code: u32) -> bool {
    matches!(
        code,
        // ASCII C0 controls, excluding \t (0x09), \n (0x0A), \r (0x0D).
        0x00..=0x08
        | 0x0B..=0x0C
        | 0x0E..=0x1F
        | 0x7F
        // C1 control block.
        | 0x80..=0x9F
        // Zero-width + bidi controls.
        | 0x200B..=0x200F
        | 0x202A..=0x202E
        | 0x2060..=0x206F
        | 0xFEFF
        | 0x180E
        | 0x1D173..=0x1D17A
        | 0xFE00..=0xFE0F
        // Unicode Tag block + supplementary variation selectors —
        // both used as primary steganographic channels.
        | 0xE0000..=0xE007F
        | 0xE0100..=0xE01EF
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_zero_width() {
        assert_eq!(sanitize_field("a\u{200B}b"), "ab");
    }

    #[test]
    fn strips_tag_block() {
        let payload: String = "safe "
            .chars()
            .chain(std::iter::once('\u{E0045}'))
            .chain("trailing".chars())
            .collect();
        assert_eq!(sanitize_field(&payload), "safe trailing");
    }

    #[test]
    fn strips_bom() {
        assert_eq!(sanitize_field("a\u{FEFF}b"), "ab");
    }

    #[test]
    fn strips_variation_selector() {
        assert_eq!(sanitize_field("x\u{FE0F}y"), "xy");
    }

    #[test]
    fn strips_ascii_del() {
        assert_eq!(sanitize_field("call\u{007F}answer"), "callanswer");
    }

    #[test]
    fn strips_c0_controls_except_whitespace() {
        assert_eq!(sanitize_field("a\u{0001}b"), "ab");
        assert_eq!(sanitize_field("a\u{001F}b"), "ab");
        // Legitimate whitespace survives.
        assert_eq!(sanitize_field("a\tb\nc\rd"), "a\tb\nc\rd");
    }

    #[test]
    fn strips_c1_block() {
        assert_eq!(sanitize_field("a\u{0085}b"), "ab");
    }

    #[test]
    fn strips_bidi_override() {
        assert_eq!(sanitize_field("a\u{202E}bcd"), "abcd");
    }

    #[test]
    fn preserves_multilingual() {
        assert_eq!(sanitize_field("Привет 世界 emoji 🎉"), "Привет 世界 emoji 🎉");
    }

    #[test]
    fn is_idempotent() {
        let raw = "store\u{200B}knowledge\u{E0049}";
        let a = sanitize_field(raw);
        let b = sanitize_field(&a);
        assert_eq!(a, b);
    }

    /// Exhaustive fuzz: walk every code point in every forbidden
    /// range and assert it is stripped when surrounded by legitimate
    /// content. Turns "caught by luck" into "guaranteed by test."
    #[test]
    fn every_declared_range_is_stripped() {
        let forbidden: &[(u32, u32, &str)] = &[
            (0x00, 0x08, "C0 low"),
            (0x0B, 0x0C, "C0 VT/FF"),
            (0x0E, 0x1F, "C0 high"),
            (0x7F, 0x7F, "DEL"),
            (0x80, 0x9F, "C1"),
            (0x200B, 0x200F, "zero-width+LRM+RLM"),
            (0x202A, 0x202E, "bidi overrides"),
            (0x2060, 0x206F, "word joiner + invisible operators"),
            (0xFEFF, 0xFEFF, "BOM"),
            (0x180E, 0x180E, "Mongolian VS"),
            (0x1D173, 0x1D17A, "musical invisible"),
            (0xFE00, 0xFE0F, "variation selectors"),
            (0xE0000, 0xE007F, "Unicode Tag block"),
            (0xE0100, 0xE01EF, "supplementary variation selectors"),
        ];
        let mut scanned = 0u32;
        for (lo, hi, label) in forbidden {
            for code in *lo..=*hi {
                let Some(c) = char::from_u32(code) else { continue };
                let raw = format!("pre{}post", c);
                let got = sanitize_field(&raw);
                assert_eq!(
                    got, "prepost",
                    "range '{label}' — U+{code:04X} survived sanitize_field"
                );
                scanned += 1;
            }
        }
        assert!(scanned > 200, "only scanned {scanned} code points");
    }

    #[test]
    fn printable_ascii_survives() {
        for code in 0x20..=0x7E {
            let c = char::from_u32(code).unwrap();
            let raw = format!("a{}b", c);
            assert_eq!(
                sanitize_field(&raw),
                raw,
                "printable U+{code:04X} stripped"
            );
        }
    }

    #[test]
    fn is_forbidden_agrees_with_sanitize_field() {
        // Sanity: the two APIs have to agree on every code point they
        // claim to reject.
        for code in 0..=0x10FFFFu32 {
            if is_forbidden(code) {
                if let Some(c) = char::from_u32(code) {
                    let s: String = c.to_string();
                    assert_eq!(
                        sanitize_field(&s),
                        "",
                        "is_forbidden says U+{code:04X} but sanitize_field didn't drop"
                    );
                }
            }
        }
    }
}
