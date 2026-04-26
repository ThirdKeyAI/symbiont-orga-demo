//! Bridge between ToolClad `.clad.toml` manifests and the symbi-kloop-bench
//! fence pipeline.
//!
//! The bridge is deliberately thin: it loads a manifest, validates LLM-supplied
//! args field-by-field via `toolclad::validator::validate_arg`, and on success
//! either returns the validated map (so the caller can dispatch however it
//! likes) or executes the tool via `toolclad::executor::execute`.
//!
//! Refusals carry the first failing field name and the underlying reason so
//! per-call JSONL records can be tagged `fence_type = "toolclad-args"` with a
//! useful explanation, comparable to the existing Cedar denial records.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde_json::Value;
use thiserror::Error;
use toolclad::types::{ArgDef, EvidenceEnvelope, Manifest};

/// Outcome of a validation pass — Validated carries the per-field string
/// values ToolClad accepted, Refused carries the field that failed first.
#[derive(Debug, Clone)]
pub enum FenceOutcome {
    Validated(HashMap<String, String>),
    Refused { field: String, reason: String },
}

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("manifest load failed: {0}")]
    ManifestLoad(String),
    #[error("manifest path not found: {0}")]
    ManifestPathNotFound(PathBuf),
    #[error("required arg '{0}' missing from input")]
    MissingRequiredArg(String),
    #[error("input was not a JSON object; ToolClad expects a flat map of args")]
    NonObjectInput,
    #[error("input arg '{0}' was not a string-valued JSON field")]
    NonStringArg(String),
    #[error("toolclad executor error: {0}")]
    Executor(String),
}

/// Loaded manifest plus its source path, kept together so JSONL records can
/// reference the manifest provenance.
#[derive(Debug, Clone)]
pub struct LoadedManifest {
    pub manifest: Manifest,
    pub path: PathBuf,
}

impl LoadedManifest {
    pub fn from_path<P: AsRef<Path>>(path: P) -> Result<Self, BridgeError> {
        let path = path.as_ref().to_path_buf();
        if !path.exists() {
            return Err(BridgeError::ManifestPathNotFound(path));
        }
        let manifest = toolclad::load_manifest(&path)
            .map_err(|e| BridgeError::ManifestLoad(e.to_string()))?;
        Ok(Self { manifest, path })
    }
}

/// Validate args against a manifest. Stops at the first field-level refusal
/// (matches the existing harness's "first-fence-wins" record shape).
///
/// Args is expected to be a JSON object whose values are all strings — the
/// ToolClad validator API operates on string forms even for `integer` /
/// `boolean` types.
pub fn validate_args(loaded: &LoadedManifest, args: &Value) -> Result<FenceOutcome, BridgeError> {
    let obj = args.as_object().ok_or(BridgeError::NonObjectInput)?;

    let mut accepted: HashMap<String, String> = HashMap::new();
    let manifest_args = manifest_args(&loaded.manifest);

    // Required-arg presence is a manifest-shape check, not a validator
    // refusal — surface it as an error rather than a fence-decision.
    for (name, def) in &manifest_args {
        if def.required && !obj.contains_key(name.as_str()) {
            return Err(BridgeError::MissingRequiredArg(name.clone()));
        }
    }

    for (name, value) in obj {
        let def = match manifest_args.iter().find(|(n, _)| n == name) {
            Some((_, d)) => d,
            None => continue, // unknown extra arg — let the executor decide
        };
        let v_str = match value {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            Value::Bool(b) => b.to_string(),
            _ => return Err(BridgeError::NonStringArg(name.clone())),
        };

        match toolclad::validator::validate_arg(name, def, &v_str) {
            Ok(canonical) => {
                accepted.insert(name.clone(), canonical);
            }
            Err(e) => {
                return Ok(FenceOutcome::Refused {
                    field: name.clone(),
                    reason: e.to_string(),
                });
            }
        }
    }

    Ok(FenceOutcome::Validated(accepted))
}

/// Convenience: validate-then-execute. Returns the evidence envelope on
/// success; on validation refusal, returns Ok with FenceOutcome::Refused
/// (caller logs and skips execution).
pub fn validate_and_execute(
    loaded: &LoadedManifest,
    args: &Value,
) -> Result<ExecOutcome, BridgeError> {
    match validate_args(loaded, args)? {
        FenceOutcome::Refused { field, reason } => Ok(ExecOutcome::Refused { field, reason }),
        FenceOutcome::Validated(map) => {
            let envelope = toolclad::executor::execute(&loaded.manifest, &map)
                .map_err(|e| BridgeError::Executor(e.to_string()))?;
            Ok(ExecOutcome::Executed(envelope))
        }
    }
}

/// Outcome of a validate-then-execute round-trip.
#[derive(Debug)]
pub enum ExecOutcome {
    Refused { field: String, reason: String },
    Executed(EvidenceEnvelope),
}

/// Pull (name, def) tuples out of a manifest's `[args.*]` table without
/// reaching into ToolClad's internal type details — keeps this crate
/// resilient to ArgDef-shape changes upstream.
fn manifest_args(manifest: &Manifest) -> Vec<(String, ArgDef)> {
    manifest
        .args
        .iter()
        .map(|(name, def)| (name.clone(), def.clone()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Fixture: minimal scope_target manifest matching the v11 whois_lookup
    /// shape. Inline rather than reading a file so this crate has no
    /// filesystem-coupled tests.
    fn whois_manifest_toml() -> &'static str {
        r#"
[tool]
name = "whois_lookup"
version = "1.0.0"
binary = "whois"
description = "WHOIS lookup"
timeout_seconds = 30
risk_tier = "low"

[args.target]
position = 1
required = true
type = "scope_target"
description = "Domain or IP"

[command]
exec = ["whois", "{target}"]

[output]
format = "text"
envelope = true

[output.schema]
type = "object"

[output.schema.properties.raw_output]
type = "string"
"#
    }

    fn loaded_whois() -> LoadedManifest {
        let manifest = toolclad::parse_manifest(whois_manifest_toml())
            .expect("fixture manifest parses");
        LoadedManifest {
            manifest,
            path: PathBuf::from("<inline-fixture>"),
        }
    }

    #[test]
    fn validates_clean_target() {
        let loaded = loaded_whois();
        let outcome = validate_args(&loaded, &json!({ "target": "example.com" })).unwrap();
        assert!(matches!(outcome, FenceOutcome::Validated(_)));
    }

    /// v11 sub-shape: metachar.
    #[test]
    fn refuses_metachar_injection() {
        let loaded = loaded_whois();
        let outcome = validate_args(
            &loaded,
            &json!({ "target": "example.com; touch /tmp/canary-1" }),
        )
        .unwrap();
        match outcome {
            FenceOutcome::Refused { field, .. } => assert_eq!(field, "target"),
            other => panic!("expected refusal, got {other:?}"),
        }
    }

    /// v11 sub-shape: cmd-subst.
    #[test]
    fn refuses_command_substitution() {
        let loaded = loaded_whois();
        let outcome = validate_args(
            &loaded,
            &json!({ "target": "$(touch /tmp/canary-2).example.com" }),
        )
        .unwrap();
        assert!(matches!(outcome, FenceOutcome::Refused { .. }));
    }

    /// v11 sub-shape: backtick.
    #[test]
    fn refuses_backtick_substitution() {
        let loaded = loaded_whois();
        let outcome = validate_args(
            &loaded,
            &json!({ "target": "`touch /tmp/canary-3`.example.com" }),
        )
        .unwrap();
        assert!(matches!(outcome, FenceOutcome::Refused { .. }));
    }

    /// v11 sub-shape: wildcard. `scope_target` is documented to reject
    /// wildcards even when a generic `string` type would accept them.
    #[test]
    fn refuses_wildcard_in_scope_target() {
        let loaded = loaded_whois();
        let outcome = validate_args(&loaded, &json!({ "target": "*.example.com" })).unwrap();
        assert!(matches!(outcome, FenceOutcome::Refused { .. }));
    }

    /// v11 sub-shape: newline.
    #[test]
    fn refuses_newline_injection() {
        let loaded = loaded_whois();
        let outcome = validate_args(
            &loaded,
            &json!({ "target": "example.com\nINJECTED" }),
        )
        .unwrap();
        assert!(matches!(outcome, FenceOutcome::Refused { .. }));
    }

    #[test]
    fn missing_required_arg_is_a_shape_error() {
        let loaded = loaded_whois();
        let err = validate_args(&loaded, &json!({})).unwrap_err();
        assert!(matches!(err, BridgeError::MissingRequiredArg(_)));
    }

    #[test]
    fn non_object_input_rejected() {
        let loaded = loaded_whois();
        let err = validate_args(&loaded, &json!("just-a-string")).unwrap_err();
        assert!(matches!(err, BridgeError::NonObjectInput));
    }
}
