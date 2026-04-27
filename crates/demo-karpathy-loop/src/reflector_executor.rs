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

use crate::knowledge::{sanitize_field, KnowledgeStore};

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
    /// Hard ceiling on successful `store_knowledge` calls per run. The
    /// default reflector prompt asks for 0–5 procedures; small models
    /// routinely ignore that cap. 0 means unlimited.
    store_cap: u32,
    /// v8 — forensic capture of raw `arguments` strings for every
    /// proposed `store_knowledge` call, **before** sanitisation. The
    /// captured payloads are intended for adversarial-sweep
    /// evaluation (per-shape bite-rate analysis) and are NOT a
    /// production audit surface. Sanitiser still runs on every
    /// downstream write (journal + store); the v6-flagged
    /// "render-hides-it" hole stays closed.
    ///
    /// `None` (the default) means no capture. The harness wires
    /// `Some(_)` only on adversarial sweeps, so default runs incur
    /// zero overhead.
    raw_args_capture: Option<Arc<Mutex<Vec<RawArgsRecord>>>>,
    /// v11 — optional pre-dispatch fence. Wired by the harness when
    /// `--toolclad-mode` is `on` or `only`. `None` is the default and
    /// preserves byte-identical pre-v11 behaviour.
    pre_validator: Option<crate::pre_validator::SharedPreValidator>,
    /// v11 — number of tool-call attempts refused by the pre-validator
    /// (typically the ToolClad typed-argument fence). Counted
    /// separately from `refused_count` so per-fence numbers stay
    /// disambiguated in the report.
    pre_validator_refused_count: Arc<Mutex<u32>>,
    /// v11 — captured pre-validator refusals for the per-call JSONL
    /// sidecar. Each entry pairs a refusal with the call id that
    /// triggered it; the harness drains the buffer the same way it
    /// drains `raw_args_capture`.
    pre_validator_refusals: Arc<Mutex<Vec<PreValidationRefusalRecord>>>,
}

/// v11 — captured refusal pairing the original call id with the
/// fence-type / reason emitted by the pre-validator. Persisted to the
/// per-call JSONL so reports can pivot bite-rate by fence.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PreValidationRefusalRecord {
    pub call_id: String,
    pub tool_name: String,
    pub fence_type: String,
    pub field: Option<String>,
    pub reason: String,
}

/// One LLM-emitted `store_knowledge` call captured **before** the
/// sanitiser ran. The harness drains the buffer at the end of a
/// reflector pass and writes it to a dedicated sidecar.
///
/// Intentionally not implementing `Display` — printing a record
/// rehydrates the unsanitised payload, which is the whole point.
/// Keep the JSON serialisation as the only surface, with a clear
/// filename suffix (`*-reflect-raw-args.jsonl`).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawArgsRecord {
    /// Tool call id from the LLM message (correlates with the journal).
    pub call_id: String,
    /// Tool name as the LLM emitted it (would be `store_knowledge`
    /// on a successful call, but a homoglyph-attack model might emit
    /// `store_knоwledge` here — also captured raw).
    pub name: String,
    /// Raw `arguments` string as the LLM produced it. NOT sanitised.
    pub arguments: String,
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
            store_cap: 0,
            raw_args_capture: None,
            pre_validator: None,
            pre_validator_refused_count: Arc::new(Mutex::new(0)),
            pre_validator_refusals: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// v11 — install a pre-dispatch fence (typically the ToolClad
    /// typed-argument bridge). `None` keeps the executor on its
    /// pre-v11 path. Refusals are counted and captured for the
    /// per-call sidecar; the LLM sees them as an error observation.
    pub fn with_pre_validator(
        mut self,
        pv: crate::pre_validator::SharedPreValidator,
    ) -> Self {
        self.pre_validator = Some(pv);
        self
    }

    /// How many tool-call attempts the pre-validator refused this run.
    pub async fn pre_validator_refused_count(&self) -> u32 {
        *self.pre_validator_refused_count.lock().await
    }

    /// Drain the captured pre-validator refusals (for the per-call
    /// JSONL sidecar). Cleared after read.
    pub async fn drain_pre_validator_refusals(
        &self,
    ) -> Vec<PreValidationRefusalRecord> {
        let mut g = self.pre_validator_refusals.lock().await;
        let out = g.clone();
        g.clear();
        out
    }

    /// Enable forensic raw-args capture for this run. Used by the
    /// harness on adversarial sweeps so the sweep report can compute
    /// per-model bite-rate (fraction of `store_knowledge` calls
    /// containing the attack payload) without compromising the
    /// sanitiser-as-content-fence story for the journal + store.
    pub fn with_raw_args_capture(mut self, buf: Arc<Mutex<Vec<RawArgsRecord>>>) -> Self {
        self.raw_args_capture = Some(buf);
        self
    }

    /// Set the per-run cap on successful `store_knowledge` calls. Once
    /// the counter reaches `cap`, further calls return an error
    /// observation and are counted as budget refusals. Pass 0 for
    /// unlimited (legacy behavior).
    pub fn with_store_cap(mut self, cap: u32) -> Self {
        self.store_cap = cap;
        self
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

    /// Drain the forensic raw-args buffer if one was wired. Returns
    /// every UNSANITISED `RawArgsRecord` captured this run (in
    /// dispatch order), then clears the buffer. The harness writes
    /// the result to `*-reflect-raw-args.jsonl`. `None` means no
    /// capture was wired (e.g. default non-adversarial run).
    pub async fn drain_raw_args(&self) -> Option<Vec<RawArgsRecord>> {
        let buf = self.raw_args_capture.as_ref()?;
        let mut g = buf.lock().await;
        let out = g.clone();
        g.clear();
        Some(out)
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

        // v8 — forensic capture BEFORE any sanitisation, before any
        // refusal logic, so a homoglyph-attack tool name (which would
        // be refused below) is also recorded. Only writes if the
        // harness wired a capture buffer.
        if let Some(buf) = &self.raw_args_capture {
            buf.lock().await.push(RawArgsRecord {
                call_id: call_id.clone(),
                name: name.clone(),
                arguments: arguments.clone(),
            });
        }

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

        // v11 — pre-dispatch typed-argument fence (ToolClad bridge,
        // when wired). Runs after Cedar permitted the action and after
        // raw-args capture, but before any sanitisation or storage.
        // Distinct from `refused_count` so per-fence bite-rate stays
        // legible in the report.
        if let Some(pv) = &self.pre_validator {
            if let Some(refusal) = pv.validate(name, arguments) {
                *self.pre_validator_refused_count.lock().await += 1;
                self.pre_validator_refusals.lock().await.push(
                    PreValidationRefusalRecord {
                        call_id: call_id.clone(),
                        tool_name: name.clone(),
                        fence_type: refusal.fence_type.clone(),
                        field: refusal.field.clone(),
                        reason: refusal.reason.clone(),
                    },
                );
                return Some(Observation {
                    source: name.clone(),
                    content: format!(
                        "tool '{}' refused at {} fence: {}{}",
                        name,
                        refusal.fence_type,
                        refusal.reason,
                        refusal
                            .field
                            .map(|f| format!(" (field: {f})"))
                            .unwrap_or_default()
                    ),
                    is_error: true,
                    call_id: Some(call_id.clone()),
                    metadata: Default::default(),
                });
            }
        }

        // v6 #2: sanitise tool-call arguments at the executor layer so
        // the cleaned values propagate to journal entries, observation
        // strings, AND the store — not just the store. Cedar permitted
        // `store_knowledge` as an action, so content-level defense
        // happens here. `sanitize_field` also runs inside
        // `KnowledgeStore::store` as belt-and-suspenders; idempotent.
        let parsed: serde_json::Value =
            serde_json::from_str(arguments).unwrap_or(serde_json::Value::Null);
        let subject = parsed
            .get("subject")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .map(sanitize_field);
        let predicate = parsed
            .get("predicate")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .map(sanitize_field);
        let object = parsed
            .get("object")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .map(sanitize_field);
        let confidence = parsed
            .get("confidence")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.8)
            .clamp(0.0, 1.0);

        // Budget fence. Once we hit the cap, refuse and count as a
        // refusal so the safety-story tallies include model misbehavior
        // around verbosity, not just tool-profile violations.
        if self.store_cap > 0 {
            let current = *self.stored_count.lock().await;
            if current >= self.store_cap {
                *self.refused_count.lock().await += 1;
                return Some(Observation {
                    source: "store_knowledge".into(),
                    content: format!(
                        "per-run store_knowledge budget exhausted ({} / {}); \
                         stop calling and return a plain-text summary instead",
                        current, self.store_cap
                    ),
                    is_error: true,
                    call_id: Some(call_id.clone()),
                    metadata: Default::default(),
                });
            }
        }

        match (subject.as_deref(), predicate.as_deref(), object.as_deref()) {
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
    async fn store_cap_enforced() {
        let td = tempfile::tempdir().unwrap();
        let ks = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        let exec = ReflectorActionExecutor::new("T1", None, ks).with_store_cap(2);
        for i in 0..5 {
            let a = ProposedAction::ToolCall {
                call_id: format!("c{i}"),
                name: "store_knowledge".into(),
                arguments: format!(
                    r#"{{"subject":"s{i}","predicate":"p","object":"o"}}"#
                ),
            };
            exec.handle_one(&a).await;
        }
        assert_eq!(exec.stored_count().await, 2);
        assert_eq!(exec.refused_count().await, 3);
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
