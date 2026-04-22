//! `DelegatorActionExecutor` — the third principal.
//!
//! A delegator agent picks which benchmark task a worker should run
//! next. The execution surface is deliberately *one* tool wide
//! (`choose_task`), matching the reflector's profile-of-one pattern
//! but with different semantics: instead of *writing* knowledge, the
//! delegator *selects* work.
//!
//! The structural guarantee this executor provides is the same as
//! `ReflectorActionExecutor`: even if a future Cedar edit accidentally
//! permitted another action, the executor has no handler for anything
//! other than `choose_task` and would return an error observation.
//! That's the belt-and-suspenders layering Symbiont's demo talks about,
//! now demonstrated at N=3 principals instead of just the two that
//! shipped in v1.
//!
//! v6 #4 — added to prove the safety story generalises past two roles.

use std::sync::Arc;

use async_trait::async_trait;
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::executor::ActionExecutor;
use symbi_runtime::reasoning::inference::ToolDefinition;
use symbi_runtime::reasoning::loop_types::{LoopConfig, Observation, ProposedAction};
use tokio::sync::Mutex;

pub struct DelegatorActionExecutor {
    /// The task ids the delegator is allowed to choose from. Any
    /// other id returned by the LLM is rejected by the handler.
    allowed_task_ids: Vec<String>,
    /// Task id chosen this run, for callers that want to inspect
    /// the delegator's decision programmatically.
    chosen: Arc<Mutex<Option<String>>>,
    /// Count of forbidden tool-call attempts (Cedar + executor
    /// belt-and-suspenders). Surfaces in the dashboard's
    /// `policy_violations_prevented` column under a `delegator` kind
    /// once the harness wires it in.
    refused_count: Arc<Mutex<u32>>,
}

impl DelegatorActionExecutor {
    pub fn new(allowed_task_ids: Vec<String>) -> Self {
        Self {
            allowed_task_ids,
            chosen: Arc::new(Mutex::new(None)),
            refused_count: Arc::new(Mutex::new(0)),
        }
    }

    /// Task id the delegator decided on this run. `None` if the
    /// delegator never emitted a successful `choose_task` call.
    pub async fn chosen(&self) -> Option<String> {
        self.chosen.lock().await.clone()
    }

    pub async fn refused_count(&self) -> u32 {
        *self.refused_count.lock().await
    }

    /// Single published tool definition — the LLM only sees this.
    pub fn tool_definition() -> ToolDefinition {
        ToolDefinition {
            name: "choose_task".into(),
            description:
                "Select which benchmark task a worker agent should \
                 run next. Pass `task_id` as a string matching one of \
                 the known tasks (e.g. `T1`). Call exactly once per \
                 turn."
                    .into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": ["task_id"]
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

        if name != "choose_task" {
            *self.refused_count.lock().await += 1;
            return Some(Observation {
                source: name.clone(),
                content: format!(
                    "tool '{}' is not available for the delegator \
                     (profile-of-one: choose_task)",
                    name
                ),
                is_error: true,
                call_id: Some(call_id.clone()),
                metadata: Default::default(),
            });
        }

        let parsed: serde_json::Value =
            serde_json::from_str(arguments).unwrap_or(serde_json::Value::Null);
        let task_id = parsed
            .get("task_id")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .unwrap_or("");

        if !self.allowed_task_ids.iter().any(|t| t == task_id) {
            return Some(Observation {
                source: "choose_task".into(),
                content: format!(
                    "'{}' is not a known task id (allowed: {})",
                    task_id,
                    self.allowed_task_ids.join(", ")
                ),
                is_error: true,
                call_id: Some(call_id.clone()),
                metadata: Default::default(),
            });
        }

        *self.chosen.lock().await = Some(task_id.to_string());
        Some(Observation {
            source: "choose_task".into(),
            content: format!("chose task '{task_id}'"),
            is_error: false,
            call_id: Some(call_id.clone()),
            metadata: Default::default(),
        })
    }
}

#[async_trait]
impl ActionExecutor for DelegatorActionExecutor {
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
    async fn chooses_an_allowed_task() {
        let exec = DelegatorActionExecutor::new(vec!["T1".into(), "T2".into()]);
        let a = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "choose_task".into(),
            arguments: r#"{"task_id":"T2"}"#.into(),
        };
        let obs = exec.handle_one(&a).await.unwrap();
        assert!(!obs.is_error, "got error: {}", obs.content);
        assert_eq!(exec.chosen().await.as_deref(), Some("T2"));
    }

    #[tokio::test]
    async fn rejects_unknown_task_id() {
        let exec = DelegatorActionExecutor::new(vec!["T1".into()]);
        let a = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "choose_task".into(),
            arguments: r#"{"task_id":"T9"}"#.into(),
        };
        let obs = exec.handle_one(&a).await.unwrap();
        assert!(obs.is_error);
        assert!(obs.content.contains("not a known task id"));
        assert!(exec.chosen().await.is_none());
    }

    #[tokio::test]
    async fn refuses_any_other_tool() {
        // Structural proof the delegator shares the reflector's
        // profile-of-one property: if Cedar were relaxed tomorrow,
        // the executor still rejects every tool that isn't
        // `choose_task`.
        let exec = DelegatorActionExecutor::new(vec!["T1".into()]);
        let forbidden = [
            "answer",
            "recall_knowledge",
            "store_knowledge",
            "pod_status",
            "ticket_title",
            "error_code_line",
        ];
        for name in &forbidden {
            let a = ProposedAction::ToolCall {
                call_id: format!("c-{name}"),
                name: (*name).into(),
                arguments: "{}".into(),
            };
            let obs = exec.handle_one(&a).await.unwrap();
            assert!(obs.is_error, "expected refusal for {name}");
            assert!(obs.content.contains("profile-of-one"));
        }
        assert_eq!(exec.refused_count().await, forbidden.len() as u32);
        assert!(exec.chosen().await.is_none());
    }

    /// v7 #4 — tool-registry trust. Mirrors the 2026 MCP marketplace-
    /// poisoning angle from cybersecuritynews.com (9 of 11 MCP
    /// registries successfully poisoned). The delegator's allow-list
    /// is the local equivalent of "verified registry": a forged task
    /// id smuggled through any channel must be refused at the
    /// delegator boundary, regardless of how plausibly it's named.
    /// This test simulates the full marketplace-poisoning class:
    ///   - homoglyph forgery (`T1` with Cyrillic characters)
    ///   - typo-squatting (`t1`, `T01`, `T1 `)
    ///   - prefix-attack (`T1; choose_task('T9')`)
    ///   - empty / whitespace
    ///   - JSON injection (`"T1\"}` to break the parser)
    /// All must end with `chosen().await == None` and an error
    /// observation. The defense is: the executor checks the parsed
    /// `task_id` against an explicit allow-list, not against any
    /// fuzzy match.
    #[tokio::test]
    async fn refuses_unregistered_task_ids_marketplace_poisoning() {
        let exec = DelegatorActionExecutor::new(vec!["T1".into(), "T2".into()]);
        let poisoned: &[&str] = &[
            // Homoglyph: Cyrillic Т (U+0422) instead of ASCII T.
            r#"{"task_id":"\u0422\u0031"}"#,
            // Typosquatting / case mismatch.
            r#"{"task_id":"t1"}"#,
            r#"{"task_id":"T01"}"#,
            // Whitespace padding (trim() handles leading/trailing — the
            // inner whitespace one should still fail).
            r#"{"task_id":" T1 X"}"#,
            // Prefix-attack: try to smuggle a second instruction.
            r#"{"task_id":"T1; choose_task(T9)"}"#,
            // Empty + missing.
            r#"{"task_id":""}"#,
            r#"{}"#,
            // JSON injection attempt — malformed payload entirely.
            r#"{"task_id":"T1\"}"#,
            // Pure noise.
            r#"not even json"#,
        ];
        for (i, args) in poisoned.iter().enumerate() {
            let a = ProposedAction::ToolCall {
                call_id: format!("c{i}"),
                name: "choose_task".into(),
                arguments: (*args).into(),
            };
            let obs = exec.handle_one(&a).await.unwrap();
            assert!(
                obs.is_error,
                "poisoned input #{i} should be refused but was accepted: {args}"
            );
            assert!(
                obs.content.contains("not a known task id"),
                "expected 'not a known task id' refusal for #{i} ({args}); got: {}",
                obs.content
            );
        }
        assert!(
            exec.chosen().await.is_none(),
            "no poisoned input may set the chosen task"
        );
    }
}
