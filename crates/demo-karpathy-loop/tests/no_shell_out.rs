//! v7 #3 — static guard against the MCP STDIO RCE shape.
//!
//! Background: the 2026 Anthropic-MCP-SDK vulnerability reported by
//! cybersecuritynews.com is an architectural arbitrary-command-
//! execution flaw — user-controllable input reaches STDIO parameters
//! that get shelled out by the MCP server process. The defense is
//! "treat all external MCP configuration input as untrusted; block
//! or restrict user-controlled inputs to STDIO parameters."
//!
//! This crate contains the three executors that make up the Symbiont
//! demo's tool surface:
//!   - `TaskActionExecutor`        (task-agent principal)
//!   - `ReflectorActionExecutor`   (reflector principal)
//!   - `DelegatorActionExecutor`   (delegator principal — v6)
//!
//! None of them shell out today. This test is the static guarantee
//! that none of them ever start to without a deliberate code review:
//! it walks every executor source file and refuses to compile if a
//! `Command::new` / `process::Command` / `Command::output` /
//! `Command::status` / `tokio::process::Command` reference appears.
//!
//! If we ever genuinely need to spawn a subprocess from an executor
//! (the way MCP servers do), the test FAILS and forces the author to
//! add an explicit allowlist entry here — at which point the
//! reviewer reads the surrounding code and either approves with
//! parameter-allowlisting in place, or rejects the change. That is
//! the "deliberate code change" gate the MCP RCE class needs.
//!
//! Companion to the other Symbiont safety fences:
//!
//!   - `policies/*.cedar`         action-level fences
//!   - `sanitize_field`           content-level fences
//!   - `lint-cedar-policies.py`   homoglyph fences
//!   - `audit-knowledge-stores.py` post-hoc audit
//!
//! This test is the *process-spawn* fence.

use std::path::PathBuf;

const FORBIDDEN_TOKENS: &[&str] = &[
    "Command::new",
    "process::Command",
    "tokio::process::Command",
    "std::process::Command",
    // Catches `use std::process::Command` followed by `Command::new`.
    "use std::process::",
    "use tokio::process::",
];

/// Files whose presence in the FORBIDDEN_TOKENS scan must be zero.
/// This intentionally hard-codes the executor surface — the test is
/// not a project-wide lint, it is a scoped guarantee about the layer
/// where MCP-style RCE would land.
fn executor_sources() -> Vec<(&'static str, PathBuf)> {
    let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    vec![
        ("executor.rs", crate_dir.join("src/executor.rs")),
        ("reflector_executor.rs", crate_dir.join("src/reflector_executor.rs")),
        ("delegator_executor.rs", crate_dir.join("src/delegator_executor.rs")),
    ]
}

#[test]
fn no_executor_shells_out() {
    let mut findings: Vec<String> = Vec::new();

    for (label, path) in executor_sources() {
        let src = std::fs::read_to_string(&path).unwrap_or_else(|e| {
            panic!("could not read executor source {}: {}", path.display(), e)
        });

        // Strip line comments so a sentinel like
        // `// previously called Command::new` doesn't false-positive.
        // Block comments are not stripped — they are vanishingly rare
        // in this crate and a future use would be flagged for review.
        let scrubbed: String = src
            .lines()
            .map(|line| {
                if let Some(idx) = line.find("//") {
                    &line[..idx]
                } else {
                    line
                }
            })
            .collect::<Vec<_>>()
            .join("\n");

        for token in FORBIDDEN_TOKENS {
            if scrubbed.contains(token) {
                findings.push(format!(
                    "{label}: contains forbidden token `{token}` — \
                     executor must not spawn subprocesses (MCP STDIO RCE \
                     class). If a subprocess is genuinely required, add \
                     an explicit allowlist entry to FORBIDDEN_TOKENS in \
                     this test file with a code-review comment justifying \
                     it AND wire parameter sanitisation at the executor \
                     layer."
                ));
            }
        }
    }

    assert!(
        findings.is_empty(),
        "executor process-spawn guard failed:\n  - {}",
        findings.join("\n  - ")
    );
}

/// Sanity: the test would actually catch a regression. We construct
/// a synthetic source line containing `Command::new` and assert our
/// scrubber does not skip it.
#[test]
fn guard_catches_synthetic_command_new() {
    let src = "fn x() { let _ = std::process::Command::new(\"sh\"); }\n";
    let scrubbed: String = src
        .lines()
        .map(|line| {
            if let Some(idx) = line.find("//") {
                &line[..idx]
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(scrubbed.contains("Command::new"));
    assert!(scrubbed.contains("process::Command"));
}

/// Sanity: a commented-out `Command::new` must NOT trip the scan,
/// otherwise existing safe sentinel comments would block builds.
#[test]
fn guard_ignores_commented_command_new() {
    let src = "fn x() {\n    // previously: let _ = Command::new(\"sh\");\n}\n";
    let scrubbed: String = src
        .lines()
        .map(|line| {
            if let Some(idx) = line.find("//") {
                &line[..idx]
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(!scrubbed.contains("Command::new"));
}
