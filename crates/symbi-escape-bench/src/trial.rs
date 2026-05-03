//! Trial-record writer. Field-for-field equivalent to
//! evals/escape/harnesses/common/trial_record.py / trial.schema.json.
//! Both substrates write the same JSON shape so the runner folds them
//! into one JSONL without per-substrate branching.

use chrono::{DateTime, Utc};
use serde::Serialize;
use std::path::Path;

#[derive(Serialize)]
pub struct ToolCall {
    pub name: String,
    pub args: serde_json::Value,
    pub result_summary: String,
    pub blocked: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub blocked_by: Option<String>,
}

#[derive(Serialize, Default)]
#[allow(dead_code)]
pub struct CanaryTrigger {
    pub vector: String,
    pub evidence: String,
}

#[derive(Serialize)]
pub struct EscapeAttempt {
    pub vector: String,
    pub evidence: String,
    pub blocked_by: Option<String>,
}

#[derive(Serialize)]
pub struct UsageRecord {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
    pub request_id: Option<String>,
    pub served_by_model: Option<String>,
}

#[derive(Serialize)]
pub struct TrialRecord {
    pub trial_id: String,
    pub task_id: String,
    pub substrate: String,
    pub model: String,
    pub prompt_variant: u32,
    pub instance_seed: u32,
    pub started_at: String,
    pub ended_at: String,
    pub model_output_trace: Vec<serde_json::Value>,
    pub tool_calls: Vec<ToolCall>,
    pub fs_trace_paths: Vec<String>,
    pub canary_triggers: Vec<CanaryTrigger>,
    pub objective_success: bool,
    pub escape_attempted: bool,
    pub escape_success: bool,
    pub escape_attempts: Vec<EscapeAttempt>,
    pub usage_per_call: Vec<UsageRecord>,
    pub time_to_first_attempt_ms: Option<u64>,
    pub time_to_escape_ms: Option<u64>,
}

pub fn now_iso() -> String {
    let now: DateTime<Utc> = Utc::now();
    now.to_rfc3339()
}

pub fn write(rec: &TrialRecord, out: &Path) -> anyhow::Result<()> {
    if let Some(parent) = out.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(out, serde_json::to_string(rec)?)?;
    Ok(())
}
