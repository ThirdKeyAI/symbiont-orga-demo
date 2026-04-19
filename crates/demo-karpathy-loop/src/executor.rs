//! `TaskActionExecutor` — the task agent's tool surface.
//!
//! The task agent can call:
//!
//! - `answer(content: string)` — commits the run's final answer and
//!   signals the loop to terminate on the next iteration.
//! - `recall_knowledge(task_id: string, limit?: number)` — reads up to
//!   `limit` (default 5) procedures previously written by the reflector.
//! - task-specific tools declared in the task's `inputs.tools` field (see
//!   `Task`'s schema). These tools have a known side-effect — for
//!   instance, `compare(a, b)` on a sort task. The actual "computation"
//!   they do is carried out here in pure Rust so the scoring stays
//!   deterministic; the LLM just orchestrates which to call.
//!
//! What the task agent **cannot** call:
//!
//! - `store_knowledge(...)` — that's reserved for the reflector. Cedar
//!   denies it structurally for this principal (see
//!   `policies/task-agent.cedar`), and the executor doesn't implement
//!   it as a second layer of defence.
//!
//! The captured `answer` payload survives after the loop exits so the
//! harness can score it without having to parse the conversation log.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::executor::ActionExecutor;
use symbi_runtime::reasoning::inference::ToolDefinition;
use symbi_runtime::reasoning::loop_types::{LoopConfig, Observation, ProposedAction};
use tokio::sync::Mutex;

use crate::knowledge::KnowledgeStore;

/// Task-agent executor.
///
/// Holds a `captured_answer` so the harness can retrieve the final
/// `answer(...)` payload without walking the conversation. The field is
/// `Mutex` only because `ActionExecutor::execute_actions` takes `&self`;
/// contention is trivial (at most one answer per run).
pub struct TaskActionExecutor {
    task_id: String,
    knowledge: KnowledgeStore,
    /// Tool handlers keyed by tool name. The task agent's tools are all
    /// pure-Rust stubs; they interpret the arguments and return a string
    /// observation. A production build would wire these to real side-effects.
    handlers: HashMap<String, ToolHandler>,
    /// Captured final answer, for `outcome()`.
    captured_answer: Arc<Mutex<Option<String>>>,
}

/// Signature for a task-domain tool handler.
///
/// Takes a JSON-encoded argument blob (as the loop passes it), returns
/// either a human-readable observation string or an error. Errors are
/// surfaced to the LLM as tool-error observations.
pub type ToolHandler = Arc<
    dyn Fn(&str) -> Result<String, String> + Send + Sync + 'static,
>;

impl TaskActionExecutor {
    pub fn new(task_id: impl Into<String>, knowledge: KnowledgeStore) -> Self {
        Self {
            task_id: task_id.into(),
            knowledge,
            handlers: HashMap::new(),
            captured_answer: Arc::new(Mutex::new(None)),
        }
    }

    /// Register a task-domain tool.
    ///
    /// Pass both the JSON-schema `ToolDefinition` the LLM will see and
    /// the handler closure that interprets argument blobs. The executor
    /// validates the name and refuses to shadow `answer` /
    /// `recall_knowledge`.
    pub fn register_tool<F>(
        &mut self,
        definition: ToolDefinition,
        handler: F,
    ) -> Result<(), anyhow::Error>
    where
        F: Fn(&str) -> Result<String, String> + Send + Sync + 'static,
    {
        let name = definition.name.clone();
        if matches!(
            name.as_str(),
            "answer" | "recall_knowledge" | "store_knowledge"
        ) {
            anyhow::bail!(
                "tool name '{}' is reserved by the runtime / executor",
                name
            );
        }
        self.handlers.insert(name, Arc::new(handler));
        // `definition` is accepted but not stored here — the harness gathers
        // `tool_definitions()` from this executor and includes the registered
        // defs by walking the same registration call sequence. Keeping the
        // canonical list in the registering code avoids any "two copies,
        // which one is authoritative" drift.
        let _ = definition;
        Ok(())
    }

    /// Pull out whatever the agent committed as its final answer.
    ///
    /// `None` means the loop ended without an `answer(...)` call (timeout,
    /// token cap, or the LLM just gave up). The grader scores `None` as 0.
    pub async fn outcome(&self) -> Option<String> {
        self.captured_answer.lock().await.clone()
    }

    /// Built-in tool definitions this executor always exposes.
    ///
    /// The harness merges these with task-specific `ToolDefinition`s
    /// registered via `register_tool` before calling `runner.run(...)`.
    pub fn builtin_definitions() -> Vec<ToolDefinition> {
        vec![
            ToolDefinition {
                name: "answer".into(),
                description:
                    "Commit your final answer for this task. Pass a single \
                     string `content`. Call this exactly once when you are \
                     confident in your answer. For tasks that expect a list, \
                     pass a JSON array serialized as a string."
                        .into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The final answer payload."
                        }
                    },
                    "required": ["content"]
                }),
            },
            ToolDefinition {
                name: "recall_knowledge".into(),
                description:
                    "Read procedures this agent has previously learned for \
                     the current task. Returns up to `limit` bullet points \
                     (default 5, max 20). Call this once near the start of a \
                     run before choosing your approach."
                        .into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "limit":   {"type": "integer", "minimum": 1, "maximum": 20}
                    },
                    "required": ["task_id"]
                }),
            },
        ]
    }

    async fn handle_one(&self, action: &ProposedAction) -> Option<Observation> {
        let ProposedAction::ToolCall {
            call_id,
            name,
            arguments,
        } = action
        else {
            // Only ToolCall actions are ours. Respond / Delegate pass through
            // the runtime untouched.
            return None;
        };

        match name.as_str() {
            "answer" => {
                let content = extract_string_arg(arguments, "content").unwrap_or_default();
                *self.captured_answer.lock().await = Some(content.clone());
                Some(Observation {
                    source: "answer".into(),
                    content: format!(
                        "Answer committed: {}",
                        truncate_for_observation(&content)
                    ),
                    is_error: false,
                    call_id: Some(call_id.clone()),
                    metadata: Default::default(),
                })
            }
            "recall_knowledge" => {
                let parsed: serde_json::Value = serde_json::from_str(arguments)
                    .unwrap_or(serde_json::Value::Null);
                let task_id = parsed
                    .get("task_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&self.task_id);
                let limit = parsed
                    .get("limit")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(5)
                    .clamp(1, 20) as usize;
                match self.knowledge.recall(task_id, limit).await {
                    Ok(procs) if procs.is_empty() => Some(Observation {
                        source: "recall_knowledge".into(),
                        content: "(no procedures learned yet for this task)".into(),
                        is_error: false,
                        call_id: Some(call_id.clone()),
                        metadata: Default::default(),
                    }),
                    Ok(procs) => {
                        let body = procs
                            .iter()
                            .map(|p| p.as_bullet())
                            .collect::<Vec<_>>()
                            .join("\n");
                        Some(Observation {
                            source: "recall_knowledge".into(),
                            content: body,
                            is_error: false,
                            call_id: Some(call_id.clone()),
                            metadata: Default::default(),
                        })
                    }
                    Err(e) => Some(Observation {
                        source: "recall_knowledge".into(),
                        content: format!("recall failed: {e}"),
                        is_error: true,
                        call_id: Some(call_id.clone()),
                        metadata: Default::default(),
                    }),
                }
            }
            other => match self.handlers.get(other) {
                Some(h) => match h(arguments) {
                    Ok(result) => Some(Observation {
                        source: other.into(),
                        content: result,
                        is_error: false,
                        call_id: Some(call_id.clone()),
                        metadata: Default::default(),
                    }),
                    Err(reason) => Some(Observation {
                        source: other.into(),
                        content: reason,
                        is_error: true,
                        call_id: Some(call_id.clone()),
                        metadata: Default::default(),
                    }),
                },
                None => Some(Observation {
                    source: other.into(),
                    content: format!(
                        "tool '{}' is not available for this agent",
                        other
                    ),
                    is_error: true,
                    call_id: Some(call_id.clone()),
                    metadata: Default::default(),
                }),
            },
        }
    }
}

#[async_trait]
impl ActionExecutor for TaskActionExecutor {
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
        // Built-ins first (answer / recall_knowledge) then registered tools
        // in insertion order. LLMs seem to be sensitive to ordering; we
        // keep the loop-terminating tool first so models that front-weight
        // early options don't ignore it.
        let mut defs = Self::builtin_definitions();
        for name in self.handlers.keys() {
            // Task-specific tools lose their rich schemas here because we
            // didn't retain the definitions. The harness registers a copy
            // of the schema in `LoopConfig.tool_definitions` before `.run()`,
            // which is the authoritative list the LLM sees. Returning just
            // the name here still satisfies the trait.
            defs.push(ToolDefinition {
                name: name.clone(),
                description: format!("task-domain tool '{}'", name),
                parameters: serde_json::json!({"type": "object"}),
            });
        }
        defs
    }
}

fn extract_string_arg(json_args: &str, key: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(json_args).ok()?;
    v.get(key).and_then(|x| x.as_str()).map(|s| s.to_string())
}

fn truncate_for_observation(s: &str) -> String {
    const MAX: usize = 200;
    if s.chars().count() <= MAX {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(MAX).collect();
        out.push('…');
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn answer_is_captured_and_acknowledged() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = TaskActionExecutor::new("T1", ks);

        let action = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "answer".into(),
            arguments: r#"{"content": "[1,2,3]"}"#.into(),
        };
        let obs = exec.handle_one(&action).await.unwrap();
        assert!(!obs.is_error);
        assert_eq!(exec.outcome().await.as_deref(), Some("[1,2,3]"));
    }

    #[tokio::test]
    async fn unknown_tool_returns_error_observation() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = TaskActionExecutor::new("T1", ks);

        let action = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "mystery".into(),
            arguments: "{}".into(),
        };
        let obs = exec.handle_one(&action).await.unwrap();
        assert!(obs.is_error);
        assert!(obs.content.contains("not available"));
    }

    #[tokio::test]
    async fn register_tool_rejects_reserved_names() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let mut exec = TaskActionExecutor::new("T1", ks);

        let def = ToolDefinition {
            name: "store_knowledge".into(),
            description: "shouldn't be allowed".into(),
            parameters: serde_json::json!({}),
        };
        assert!(exec.register_tool(def, |_| Ok("ok".into())).is_err());
    }
}
