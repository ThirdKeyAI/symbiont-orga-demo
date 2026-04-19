//! `ReflectorActionExecutor` — tool profile of exactly one.
//!
//! This is the safety-critical half of the demo. The reflector agent
//! exists solely to propose new procedures after each task run. Its
//! executor therefore exposes `store_knowledge` and *nothing else* —
//! no `answer`, no `recall_knowledge`, no task-domain tools, no
//! filesystem, no HTTP.
//!
//! Cedar policy (`policies/reflector.cedar`) enforces the same rule
//! at the gate layer. The executor is belt-and-suspenders for it:
//! even if a future change to the Cedar file accidentally permitted
//! another action, the executor still has no handler for anything
//! other than `store_knowledge` and would return a tool-error
//! observation to the LLM.
//!
//! That "structurally unreachable" property is the money line for the
//! demo: the reflector can teach the task agent new procedures; it
//! can't teach itself new capabilities.

use std::sync::Arc;

use async_trait::async_trait;
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::executor::ActionExecutor;
use symbi_runtime::reasoning::inference::ToolDefinition;
use symbi_runtime::reasoning::loop_types::{LoopConfig, Observation, ProposedAction};
use tokio::sync::Mutex;

use crate::knowledge::KnowledgeStore;

/// One run's worth of reflector state.
pub struct ReflectorActionExecutor {
    task_id: String,
    run_id: Option<i64>,
    knowledge: KnowledgeStore,
    /// Count of `store_knowledge` calls that succeeded this run. The
    /// harness pulls this out after `.run()` returns for the dashboard
    /// `stored_procedures` column.
    stored_count: Arc<Mutex<u32>>,
    /// Count of tool-call attempts the executor refused because the
    /// tool name wasn't `store_knowledge`. This is the "policy violations
    /// prevented" number in the proof artifact — the reflector tried
    /// to call something it shouldn't, and the system stopped it.
    refused_count: Arc<Mutex<u32>>,
}

impl ReflectorActionExecutor {
    pub fn new(
        task_id: impl Into<String>,
        run_id: Option<i64>,
        knowledge: KnowledgeStore,
    ) -> Self {
        Self {
            task_id: task_id.into(),
            run_id,
            knowledge,
            stored_count: Arc::new(Mutex::new(0)),
            refused_count: Arc::new(Mutex::new(0)),
        }
    }

    /// How many procedures landed in the knowledge store this run.
    pub async fn stored_count(&self) -> u32 {
        *self.stored_count.lock().await
    }

    /// How many tool calls the reflector tried to make that weren't
    /// `store_knowledge`. Each one is a Cedar/executor refusal — the
    /// demo's proof that the policy held.
    pub async fn refused_count(&self) -> u32 {
        *self.refused_count.lock().await
    }

    /// Single published tool definition — the LLM only sees this.
    pub fn tool_definition() -> ToolDefinition {
        ToolDefinition {
            name: "store_knowledge".into(),
            description:
                "Record a single concrete, actionable procedure the task agent \
                 should remember for similar future tasks. Use subject-predicate-object \
                 form; keep each field under 60 characters."
                    .into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {
                    "subject":    {"type": "string"},
                    "predicate":  {"type": "string"},
                    "object":     {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0}
                },
                "required": ["subject", "predicate", "object"]
            }),
        }
    }

    async fn handle_one(&self, action: &ProposedAction) -> Option<Observation> {
        let ProposedAction::ToolCall {
            call_id,
            name,
            arguments,
        } = action
        else {
            return None;
        };

        if name != "store_knowledge" {
            // Structural refusal. Cedar should have already blocked this,
            // but we fail closed here as a second layer. We count the
            // attempt so the demo can point to a non-zero number.
            *self.refused_count.lock().await += 1;
            return Some(Observation {
                source: name.clone(),
                content: format!(
                    "tool '{}' is not available for the reflector (profile-of-one: \
                     store_knowledge)",
                    name
                ),
                is_error: true,
                call_id: Some(call_id.clone()),
                metadata: Default::default(),
            });
        }

        let parsed: serde_json::Value =
            serde_json::from_str(arguments).unwrap_or(serde_json::Value::Null);
        let subject = parsed
            .get("subject")
            .and_then(|v| v.as_str())
            .map(str::trim);
        let predicate = parsed
            .get("predicate")
            .and_then(|v| v.as_str())
            .map(str::trim);
        let object = parsed
            .get("object")
            .and_then(|v| v.as_str())
            .map(str::trim);
        let confidence = parsed
            .get("confidence")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.8)
            .clamp(0.0, 1.0);

        match (subject, predicate, object) {
            (Some(s), Some(p), Some(o)) if !s.is_empty() && !p.is_empty() && !o.is_empty() => {
                match self
                    .knowledge
                    .store(&self.task_id, self.run_id, s, p, o, confidence)
                    .await
                {
                    Ok(id) => {
                        *self.stored_count.lock().await += 1;
                        Some(Observation {
                            source: "store_knowledge".into(),
                            content: format!("stored procedure #{id}"),
                            is_error: false,
                            call_id: Some(call_id.clone()),
                            metadata: Default::default(),
                        })
                    }
                    Err(e) => Some(Observation {
                        source: "store_knowledge".into(),
                        content: format!("storage error: {e}"),
                        is_error: true,
                        call_id: Some(call_id.clone()),
                        metadata: Default::default(),
                    }),
                }
            }
            _ => Some(Observation {
                source: "store_knowledge".into(),
                content:
                    "missing or empty subject/predicate/object; no knowledge stored"
                        .into(),
                is_error: true,
                call_id: Some(call_id.clone()),
                metadata: Default::default(),
            }),
        }
    }
}

#[async_trait]
impl ActionExecutor for ReflectorActionExecutor {
    async fn execute_actions(
        &self,
        actions: &[ProposedAction],
        _config: &LoopConfig,
        _circuit_breakers: &CircuitBreakerRegistry,
    ) -> Vec<Observation> {
        let mut out = Vec::with_capacity(actions.len());
        for a in actions {
            if let Some(obs) = self.handle_one(a).await {
                out.push(obs);
            }
        }
        out
    }

    fn tool_definitions(&self) -> Vec<ToolDefinition> {
        vec![Self::tool_definition()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn stores_valid_triple() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = ReflectorActionExecutor::new("T1", Some(7), ks.clone());

        let a = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "store_knowledge".into(),
            arguments: r#"{"subject":"sort","predicate":"before","object":"sum"}"#.into(),
        };
        let obs = exec.handle_one(&a).await.unwrap();
        assert!(!obs.is_error, "got error: {}", obs.content);
        assert_eq!(exec.stored_count().await, 1);
        let got = ks.recall("T1", 5).await.unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].subject, "sort");
        assert_eq!(got[0].learned_at_run_id, Some(7));
    }

    #[tokio::test]
    async fn refuses_any_other_tool() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = ReflectorActionExecutor::new("T1", None, ks);

        for forbidden in &["answer", "recall_knowledge", "http_get", "compare"] {
            let a = ProposedAction::ToolCall {
                call_id: "c".into(),
                name: (*forbidden).into(),
                arguments: "{}".into(),
            };
            let obs = exec.handle_one(&a).await.unwrap();
            assert!(obs.is_error, "expected refusal for {forbidden}");
            assert!(obs.content.contains("profile-of-one"));
        }
        assert_eq!(exec.refused_count().await, 4);
        assert_eq!(exec.stored_count().await, 0);
    }

    #[tokio::test]
    async fn empty_fields_are_rejected() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = ReflectorActionExecutor::new("T1", None, ks);

        let a = ProposedAction::ToolCall {
            call_id: "c".into(),
            name: "store_knowledge".into(),
            arguments: r#"{"subject":"","predicate":"p","object":"o"}"#.into(),
        };
        let obs = exec.handle_one(&a).await.unwrap();
        assert!(obs.is_error);
        assert_eq!(exec.stored_count().await, 0);
    }
}
