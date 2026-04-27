//! v11 — pre-execution validator hook.
//!
//! A `PreValidator` runs after Cedar has permitted a `tool_call` action
//! but before the action executor dispatches it. The hook reports a
//! refusal with a `fence_type` tag and a reason; the action executor
//! converts that into a tool-call error observation, exactly the way
//! Cedar denials and budget-cap refusals are surfaced today.
//!
//! This crate stays free of any specific validator implementation —
//! the bench layer (`symbi-kloop-bench`) registers a concrete
//! `PreValidator` that wraps `symbi-toolclad-bridge`.

use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct PreValidationRefusal {
    /// Fence taxonomy tag for per-call JSONL — e.g. "toolclad-args".
    pub fence_type: String,
    /// Which input field, if any, caused the refusal. Optional because
    /// some validators refuse on shape rather than a single field.
    pub field: Option<String>,
    /// Human-readable explanation surfaced to the LLM as a tool-call
    /// error observation, and persisted to the JSONL record.
    pub reason: String,
}

pub trait PreValidator: Send + Sync {
    /// Returns `Some(refusal)` to short-circuit the call, `None` to
    /// allow it. The arguments string is the raw JSON the LLM emitted
    /// — same shape Cedar and the existing executor see.
    fn validate(&self, tool_name: &str, arguments_json: &str)
        -> Option<PreValidationRefusal>;
}

/// Convenience: an `Arc<dyn PreValidator>` is the carry-shape used by
/// every executor that opts into the hook.
pub type SharedPreValidator = Arc<dyn PreValidator>;
