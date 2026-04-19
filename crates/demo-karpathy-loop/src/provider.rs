//! `MockInferenceProvider` — deterministic offline provider for the demo.
//!
//! Why a mock provider ships with the crate:
//!
//! - The demo needs to run end-to-end without a live LLM API key. That
//!   makes CI runs meaningful and lets contributors reproduce the
//!   improvement curve on a plane.
//! - The improvement signal has to be *legible*. A real LLM will improve
//!   stochastically; the mock produces a clean, reproducible curve so the
//!   dashboard and report are readable on the first pass.
//! - We want to demonstrate that the reflector's `store_knowledge` calls
//!   actually feed back into the next run. A mock that reads the injected
//!   knowledge messages out of its own conversation history is the
//!   simplest way to show that loop closing.
//!
//! The mock is a simple state machine per-call:
//!
//! 1. If the conversation's most-recent user message contains a
//!    `task_id=T…` marker, route to the script for that task.
//! 2. If the system/assistant history shows learned procedures from
//!    `recall_knowledge`, take the **short path** for that task.
//! 3. Otherwise, take the **long path**.
//!
//! A "script" is a vector of [`ScriptStep`]s the provider plays back
//! one iteration at a time. Callers can swap in a real
//! `CloudInferenceProvider` for the demo's hero run by setting
//! `--provider cloud` on the bench binary.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use symbi_runtime::reasoning::conversation::{Conversation, MessageRole};
use symbi_runtime::reasoning::inference::{
    FinishReason, InferenceError, InferenceOptions, InferenceProvider, InferenceResponse,
    ToolCallRequest, Usage,
};
use tokio::sync::Mutex;

/// A single scripted turn the mock plays back.
#[derive(Debug, Clone)]
pub enum ScriptStep {
    /// Emit a `ToolCall` inference response. The loop will dispatch it,
    /// feed the observation back to the provider, and advance us to the
    /// next step.
    Tool {
        name: String,
        arguments: serde_json::Value,
        /// Synthetic prompt/completion token counts for this turn. Drives
        /// the "tokens trending down" dashboard line; pick numbers that
        /// roughly track real-world provider costs (Claude Haiku ≈ 200
        /// tokens per turn of this shape is a fair rough).
        prompt_tokens: u32,
        completion_tokens: u32,
    },
    /// Emit a terminating text response. Typically the run's last message
    /// — the agent has already committed an answer via a `Tool` step and
    /// is now "wrapping up" with plain prose.
    Finish {
        content: String,
        prompt_tokens: u32,
        completion_tokens: u32,
    },
}

/// Per-task script pair: long path (cold start) and short path (learned).
#[derive(Debug, Clone)]
pub struct TaskScript {
    /// What the agent does on the first run for this task.
    pub long: Vec<ScriptStep>,
    /// What the agent does once the reflector has populated knowledge.
    pub short: Vec<ScriptStep>,
    /// Hook word used by `TaskScript::picks_short_path` to decide whether
    /// the conversation has already been hydrated with enough knowledge
    /// to take the efficient route. Match is case-insensitive substring.
    pub learned_marker: String,
    /// What the reflector itself does on this task after the task agent's
    /// run completes. Separate from `long`/`short` because the reflector's
    /// tool profile is different (`store_knowledge` only).
    pub reflector: Vec<ScriptStep>,
}

/// Key: `(task_id, role_tag)`. Value: `(took_short_path, next_step_idx)`.
type CursorMap = HashMap<(String, &'static str), (bool, usize)>;

/// The mock provider.
///
/// `script_cursor` tracks playback position per-task per-role. Keying on
/// `(task_id, role)` means the same provider instance can serve both the
/// task agent and the reflector during a demo iteration without the two
/// stepping on each other.
pub struct MockInferenceProvider {
    /// Immutable script bundle.
    scripts: HashMap<String, TaskScript>,
    /// Playback state keyed by `(task_id, role_tag)`.
    cursors: Mutex<CursorMap>,
}

impl MockInferenceProvider {
    /// Start with no scripts registered. Call `.register(task_id, script)`
    /// for every task in the benchmark before invoking the loop.
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            scripts: HashMap::new(),
            cursors: Mutex::new(HashMap::new()),
        })
    }

    /// Construct a provider pre-loaded with `scripts`.
    pub fn with_scripts(scripts: HashMap<String, TaskScript>) -> Arc<Self> {
        Arc::new(Self {
            scripts,
            cursors: Mutex::new(HashMap::new()),
        })
    }

    /// Return a clone of the registered script bundle. Used by the harness
    /// to mint a fresh provider per iteration — the cursor state is
    /// deliberately per-instance, so callers that want "deterministic
    /// playback from scratch for every run" should construct a new
    /// provider via `with_scripts(p.scripts_clone())` on each invocation.
    pub fn scripts_clone(&self) -> HashMap<String, TaskScript> {
        self.scripts.clone()
    }

    /// Rewind every cursor back to the start of its branch. Cheaper
    /// alternative to building a fresh provider when the caller owns an
    /// `Arc<MockInferenceProvider>` directly.
    pub async fn reset_cursors(&self) {
        self.cursors.lock().await.clear();
    }

    /// Extract the task id marker from the most recent user message.
    ///
    /// The harness sets this up by starting each conversation with a user
    /// message whose first line is `task_id=<id>`. Anything else is fine
    /// too — the mock only needs *some* hook.
    fn task_id_from_conversation(conversation: &Conversation) -> Option<String> {
        for msg in conversation.messages().iter().rev() {
            if msg.role == MessageRole::User {
                for line in msg.content.lines() {
                    let trimmed = line.trim();
                    if let Some(rest) = trimmed.strip_prefix("task_id=") {
                        let id = rest.trim();
                        if !id.is_empty() {
                            return Some(id.to_string());
                        }
                    }
                }
            }
        }
        None
    }

    /// Role tag used by the cursor key. We infer the role from the system
    /// prompt: if it mentions the word "reflector" (case-insensitive), we
    /// treat this invocation as the reflector. Otherwise task agent.
    ///
    /// Explicit, narrow sniff rather than a flag so the provider has
    /// zero shared mutable configuration between invocations.
    fn role_tag_from_conversation(conversation: &Conversation) -> &'static str {
        for msg in conversation.messages() {
            if msg.role == MessageRole::System
                && msg.content.to_ascii_lowercase().contains("reflector")
            {
                return "reflector";
            }
        }
        "task_agent"
    }

    /// Decide whether recalled knowledge is "enough" to take the short
    /// path on this task. Looks for `learned_marker` in any observation
    /// or recall_knowledge tool-result message.
    fn picks_short_path(conversation: &Conversation, marker: &str) -> bool {
        if marker.is_empty() {
            return false;
        }
        let needle = marker.to_ascii_lowercase();
        for msg in conversation.messages() {
            if msg.role == MessageRole::Tool && msg.content.to_ascii_lowercase().contains(&needle)
            {
                return true;
            }
        }
        false
    }
}

#[async_trait]
impl InferenceProvider for MockInferenceProvider {
    async fn complete(
        &self,
        conversation: &Conversation,
        _options: &InferenceOptions,
    ) -> Result<InferenceResponse, InferenceError> {
        let task_id = Self::task_id_from_conversation(conversation).ok_or_else(|| {
            InferenceError::InvalidRequest(
                "MockInferenceProvider requires the user message to include 'task_id=<id>'".into(),
            )
        })?;
        let role_tag = Self::role_tag_from_conversation(conversation);

        let script = self.scripts.get(&task_id).ok_or_else(|| {
            InferenceError::InvalidRequest(format!(
                "no script registered for task_id '{task_id}'"
            ))
        })?;

        // Pick which branch of the script to walk on first iteration.
        // On subsequent iterations (cursor non-zero), stay on whichever
        // branch we committed to — switching mid-run would produce
        // incoherent scripts.
        let short_path = Self::picks_short_path(conversation, &script.learned_marker);

        // Locate the current step. The cursor records (took_short, index).
        let steps: &Vec<ScriptStep> = match role_tag {
            "reflector" => &script.reflector,
            _ if short_path => &script.short,
            _ => &script.long,
        };

        let (branch_tag, step) = {
            let mut cursors = self.cursors.lock().await;
            let entry = cursors
                .entry((task_id.clone(), role_tag))
                .or_insert((short_path, 0));
            // If the branch we're on this call differs from the one we
            // previously cached (possible on the very first call — no
            // previous entry), respect the newly computed branch and
            // reset the index.
            if entry.0 != short_path && entry.1 == 0 {
                entry.0 = short_path;
            }
            let (took_short, idx) = *entry;
            let step = steps.get(idx).cloned();
            if step.is_some() {
                entry.1 = idx + 1;
            }
            (took_short, step)
        };
        let _ = branch_tag;

        let step = step.unwrap_or(ScriptStep::Finish {
            content: "(script exhausted; terminating)".into(),
            prompt_tokens: 50,
            completion_tokens: 10,
        });

        Ok(match step {
            ScriptStep::Tool {
                name,
                arguments,
                prompt_tokens,
                completion_tokens,
            } => InferenceResponse {
                content: String::new(),
                tool_calls: vec![ToolCallRequest {
                    id: format!("mock_{}", uuid::Uuid::new_v4()),
                    name,
                    arguments: arguments.to_string(),
                }],
                finish_reason: FinishReason::ToolCalls,
                usage: Usage {
                    prompt_tokens,
                    completion_tokens,
                    total_tokens: prompt_tokens + completion_tokens,
                },
                model: "mock".into(),
            },
            ScriptStep::Finish {
                content,
                prompt_tokens,
                completion_tokens,
            } => InferenceResponse {
                content,
                tool_calls: Vec::new(),
                finish_reason: FinishReason::Stop,
                usage: Usage {
                    prompt_tokens,
                    completion_tokens,
                    total_tokens: prompt_tokens + completion_tokens,
                },
                model: "mock".into(),
            },
        })
    }

    fn provider_name(&self) -> &str {
        "mock"
    }

    fn default_model(&self) -> &str {
        "mock"
    }

    fn supports_native_tools(&self) -> bool {
        true
    }

    fn supports_structured_output(&self) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use symbi_runtime::reasoning::conversation::{Conversation, ConversationMessage};

    fn toy_script() -> TaskScript {
        TaskScript {
            long: vec![
                ScriptStep::Tool {
                    name: "compare".into(),
                    arguments: serde_json::json!({"a": 1, "b": 2}),
                    prompt_tokens: 100,
                    completion_tokens: 20,
                },
                ScriptStep::Tool {
                    name: "answer".into(),
                    arguments: serde_json::json!({"content": "[1,2]"}),
                    prompt_tokens: 100,
                    completion_tokens: 10,
                },
            ],
            short: vec![ScriptStep::Tool {
                name: "answer".into(),
                arguments: serde_json::json!({"content": "[1,2]"}),
                prompt_tokens: 30,
                completion_tokens: 5,
            }],
            learned_marker: "learned_marker".into(),
            reflector: vec![ScriptStep::Tool {
                name: "store_knowledge".into(),
                arguments: serde_json::json!({
                    "subject": "sort",
                    "predicate": "before",
                    "object": "sum"
                }),
                prompt_tokens: 40,
                completion_tokens: 15,
            }],
        }
    }

    #[tokio::test]
    async fn cold_start_takes_long_path() {
        let mut scripts = HashMap::new();
        scripts.insert("T1".into(), toy_script());
        let p = MockInferenceProvider::with_scripts(scripts);

        let mut conv = Conversation::new();
        conv.push(ConversationMessage::user("task_id=T1"));

        let opts = InferenceOptions::default();
        let r1 = p.complete(&conv, &opts).await.unwrap();
        assert_eq!(r1.tool_calls.len(), 1);
        assert_eq!(r1.tool_calls[0].name, "compare");

        let r2 = p.complete(&conv, &opts).await.unwrap();
        assert_eq!(r2.tool_calls[0].name, "answer");
    }

    #[tokio::test]
    async fn marker_in_tool_message_flips_to_short_path() {
        let mut scripts = HashMap::new();
        scripts.insert("T1".into(), toy_script());
        let p = MockInferenceProvider::with_scripts(scripts);

        let mut conv = Conversation::new();
        conv.push(ConversationMessage::user("task_id=T1"));
        // Simulate a prior recall_knowledge observation surfacing the marker.
        conv.push(ConversationMessage::tool_result(
            "c1",
            "recall_knowledge",
            "- sort learned_marker sum (confidence 0.90)",
        ));

        let r = p.complete(&conv, &InferenceOptions::default()).await.unwrap();
        assert_eq!(r.tool_calls[0].name, "answer");
    }

    #[tokio::test]
    async fn reflector_branch_runs_on_reflector_system_prompt() {
        let mut scripts = HashMap::new();
        scripts.insert("T1".into(), toy_script());
        let p = MockInferenceProvider::with_scripts(scripts);

        let mut conv = Conversation::with_system("You are the REFLECTOR agent.");
        conv.push(ConversationMessage::user("task_id=T1"));

        let r = p.complete(&conv, &InferenceOptions::default()).await.unwrap();
        assert_eq!(r.tool_calls[0].name, "store_knowledge");
    }
}
