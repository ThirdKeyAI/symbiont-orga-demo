//! v11 — bench-side adapter that turns a `LoadedManifest` into a
//! `PreValidator` the executors can hold. The actual validation logic
//! lives in `symbi-toolclad-bridge`; this module only wires the
//! refusal shape into `demo-karpathy-loop`'s neutral
//! `PreValidationRefusal` type so the runtime crate stays free of
//! ToolClad imports.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use demo_karpathy_loop::{PreValidationRefusal, PreValidator};
use symbi_toolclad_bridge::{validate_args, FenceOutcome, LoadedManifest};

/// One pre-validator per (tool-name → manifest) mapping. The bench
/// constructs it once at start-up and shares it with every executor
/// that needs the typed-argument fence.
pub struct ToolCladFence {
    by_tool: HashMap<String, LoadedManifest>,
}

impl ToolCladFence {
    /// Build a fence from a tool-name → manifest-path map. Any
    /// missing manifest path results in an error so misconfiguration
    /// surfaces immediately rather than at first call.
    pub fn from_paths(
        manifests_dir: &Path,
        mappings: &[(&str, &str)],
    ) -> Result<Self, String> {
        let mut by_tool = HashMap::new();
        for (tool_name, file_name) in mappings {
            let path: PathBuf = manifests_dir.join(file_name);
            let loaded = LoadedManifest::from_path(&path)
                .map_err(|e| format!("loading manifest for '{tool_name}': {e}"))?;
            by_tool.insert((*tool_name).to_string(), loaded);
        }
        Ok(Self { by_tool })
    }

    /// Wrap the fence in an `Arc<dyn PreValidator>` for executor
    /// installation.
    pub fn shared(self) -> Arc<dyn PreValidator> {
        Arc::new(self)
    }

    /// Number of tools the fence will validate. Used by harness
    /// startup logging.
    pub fn tool_count(&self) -> usize {
        self.by_tool.len()
    }
}

impl PreValidator for ToolCladFence {
    fn validate(
        &self,
        tool_name: &str,
        arguments_json: &str,
    ) -> Option<PreValidationRefusal> {
        let loaded = self.by_tool.get(tool_name)?;
        // Best-effort JSON parse. If the LLM emitted unparseable JSON
        // (rare; the tool API normally hands us validated-shape JSON),
        // refuse — the call cannot proceed safely.
        let parsed: serde_json::Value =
            match serde_json::from_str(arguments_json) {
                Ok(v) => v,
                Err(e) => {
                    return Some(PreValidationRefusal {
                        fence_type: "toolclad-args".into(),
                        field: None,
                        reason: format!(
                            "tool arguments are not valid JSON: {e}"
                        ),
                    });
                }
            };
        match validate_args(loaded, &parsed) {
            Ok(FenceOutcome::Validated(_)) => None,
            Ok(FenceOutcome::Refused { field, reason }) => {
                Some(PreValidationRefusal {
                    fence_type: "toolclad-args".into(),
                    field: Some(field),
                    reason,
                })
            }
            // Shape errors (missing required, non-object) are also
            // refusals at the same fence layer — surface them with the
            // same fence_type so the report doesn't have to special-
            // case them.
            Err(e) => Some(PreValidationRefusal {
                fence_type: "toolclad-args".into(),
                field: None,
                reason: e.to_string(),
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixtures_dir() -> PathBuf {
        // Walk up from the test binary to the repo root manifests/
        // dir. cargo runs tests from the crate dir, so two levels up.
        let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        p.pop();
        p.pop();
        p.push("manifests");
        p
    }

    #[test]
    fn loads_real_repo_manifests() {
        let fence = ToolCladFence::from_paths(
            &fixtures_dir(),
            &[
                ("store_knowledge", "store_knowledge.clad.toml"),
                ("whois_lookup", "whois_lookup.clad.toml"),
            ],
        )
        .expect("manifests load");
        assert_eq!(fence.tool_count(), 2);
    }

    #[test]
    fn refuses_metachar_in_whois_target() {
        let fence = ToolCladFence::from_paths(
            &fixtures_dir(),
            &[("whois_lookup", "whois_lookup.clad.toml")],
        )
        .expect("manifest loads");
        let refusal = fence
            .validate(
                "whois_lookup",
                r#"{"target":"example.com; touch /tmp/canary-test"}"#,
            )
            .expect("expected refusal");
        assert_eq!(refusal.fence_type, "toolclad-args");
    }

    #[test]
    fn allows_clean_target() {
        let fence = ToolCladFence::from_paths(
            &fixtures_dir(),
            &[("whois_lookup", "whois_lookup.clad.toml")],
        )
        .expect("manifest loads");
        assert!(fence
            .validate("whois_lookup", r#"{"target":"example.com"}"#)
            .is_none());
    }

    #[test]
    fn unmapped_tool_passes_through() {
        // Fence only knows about whois_lookup; calling a different
        // tool returns None so the call proceeds via the existing
        // executor logic. Used for ToolCladMode::On where un-ported
        // tools still need to work.
        let fence = ToolCladFence::from_paths(
            &fixtures_dir(),
            &[("whois_lookup", "whois_lookup.clad.toml")],
        )
        .expect("manifest loads");
        assert!(fence
            .validate("recall_knowledge", r#"{"task_id":"T1"}"#)
            .is_none());
    }
}
