//! `OpenRouterInferenceProvider` — OpenAI-compat client that also
//! captures the extra per-request metadata OpenRouter returns.
//!
//! Why this exists when we already have a generic `CloudInferenceProvider`:
//!
//! - OpenRouter responses carry three extra fields we want for the
//!   report: `id` (generation id, usable with
//!   `GET /api/v1/generation?id=<id>` for deep forensics), `provider`
//!   (which upstream actually served the call — "Azure" vs
//!   "Together" etc.), and `usage.cost` (authoritative billed USD).
//!   The shared `CloudInferenceProvider` throws those away because its
//!   `Usage` struct doesn't model them.
//!
//! - We persist one line per inference call into a JSONL sidecar file
//!   so a post-hoc Python script can correlate iteration counts,
//!   termination reasons, and actual spend without hitting the
//!   OpenRouter API again.
//!
//! - Runtime config: `OPENROUTER_API_KEY` (required),
//!   `OPENROUTER_MODEL` (model id), `OPENROUTER_BASE_URL`
//!   (defaults to <https://openrouter.ai/api/v1>).

use std::sync::Arc;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::inference::{
    FinishReason, InferenceError, InferenceOptions, InferenceProvider, InferenceResponse,
    ResponseFormat, ToolCallRequest, Usage,
};
use tokio::sync::Mutex;

/// Per-call record written to the sidecar JSONL. One line per inference
/// response. The harness stamps task_id / run_number / kind externally
/// when it drains the log so this struct stays provider-agnostic.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallLog {
    /// Milliseconds since UNIX epoch of the *response*.
    pub completed_at_ms: i64,
    /// OpenRouter generation id, usable with
    /// `GET /api/v1/generation?id=<id>`.
    pub generation_id: Option<String>,
    /// Upstream provider that actually served the call (Azure, Together,
    /// Parasail, etc.). Surfaces load-balancing behaviour.
    pub upstream_provider: Option<String>,
    /// Model id as the provider reported it (may differ from requested).
    pub model: String,
    /// Authoritative billed USD for this call (OpenRouter's
    /// `usage.cost`). 0 when missing.
    pub cost_usd: f64,
    /// Token split.
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
    pub total_tokens: u32,
    /// Wall-clock latency of the HTTP call (ms).
    pub latency_ms: u64,
    /// Tool-call count emitted by this response (0 = pure text reply).
    pub tool_calls_emitted: u32,
    /// Finish reason as OpenRouter reported it.
    pub finish_reason: String,
    /// HTTP status code of the response (for debugging 429/5xx).
    pub http_status: u16,
}

/// Per-iteration context the harness stamps onto every OpenRouter
/// request. Groups all inference calls for one task-agent or reflector
/// run into a single session in observability dashboards
/// (Langfuse/Helicone/PostHog wired to OpenRouter's broadcast feature),
/// and labels the environment so default vs adversarial sweeps are
/// distinguishable in the same dashboard.
#[derive(Debug, Clone, Default)]
pub struct TraceContext {
    /// Task id (T1, T2, ...).
    pub task_id: String,
    /// Demo iteration number (1, 2, 3, ...).
    pub run_number: u32,
    /// "task-agent" or "reflector".
    pub role: String,
    /// Free-form environment label shipped as `trace.environment`.
    /// The sweep script sets this via `OPENROUTER_TRACE_ENV` to
    /// distinguish e.g. `v2-default` from `v2-adversarial`.
    pub environment: String,
}

pub struct OpenRouterInferenceProvider {
    base_url: String,
    model: String,
    api_key: String,
    http: reqwest::Client,
    /// Per-call log; harness drains this after each run.
    calls: Arc<Mutex<Vec<CallLog>>>,
    /// Per-iteration trace labels; set by the harness before each
    /// ReasoningLoopRunner.run() call.
    trace: Arc<Mutex<Option<TraceContext>>>,
}

impl OpenRouterInferenceProvider {
    pub fn from_env() -> anyhow::Result<Self> {
        let api_key = std::env::var("OPENROUTER_API_KEY")
            .map_err(|_| anyhow::anyhow!("OPENROUTER_API_KEY not set"))?;
        let model = std::env::var("OPENROUTER_MODEL").unwrap_or_else(|_| {
            tracing::warn!("OPENROUTER_MODEL not set; defaulting to anthropic/claude-sonnet-4");
            "anthropic/claude-sonnet-4".into()
        });
        let base_url = std::env::var("OPENROUTER_BASE_URL")
            .unwrap_or_else(|_| "https://openrouter.ai/api/v1".into());
        Ok(Self::new(base_url, model, api_key))
    }

    pub fn new(
        base_url: impl Into<String>,
        model: impl Into<String>,
        api_key: impl Into<String>,
    ) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()
            .expect("reqwest client");
        Self {
            base_url: base_url.into(),
            model: model.into(),
            api_key: api_key.into(),
            http,
            calls: Arc::new(Mutex::new(Vec::new())),
            trace: Arc::new(Mutex::new(None)),
        }
    }

    /// Shared handle to the call log. Cheap `Arc::clone`.
    pub fn calls(&self) -> Arc<Mutex<Vec<CallLog>>> {
        self.calls.clone()
    }

    /// Drain and return the log so the harness can persist it to disk.
    pub async fn drain_calls(&self) -> Vec<CallLog> {
        let mut g = self.calls.lock().await;
        std::mem::take(&mut *g)
    }

    /// Install the trace context for subsequent calls. Every inference
    /// made after this point will carry these labels until the next
    /// `set_trace_context` replaces them.
    pub async fn set_trace_context(&self, ctx: TraceContext) {
        *self.trace.lock().await = Some(ctx);
    }

    fn build_body(
        &self,
        conversation: &Conversation,
        options: &InferenceOptions,
        trace: Option<&TraceContext>,
    ) -> serde_json::Value {
        let model = options.model.as_deref().unwrap_or(&self.model);

        let mut body = serde_json::json!({
            "model": model,
            "messages": conversation.to_openai_messages(),
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
            // OpenRouter-specific: ask the API to include upstream
            // cost/provider in every response. Free, opt-in.
            "usage": { "include": true },
        });

        // Broadcast trace fields: observability dashboards hooked to
        // OpenRouter pick these up automatically.
        let user = std::env::var("OPENROUTER_USER")
            .unwrap_or_else(|_| "symbiont-orga-demo".into());
        body["user"] = serde_json::Value::String(user);

        if let Some(t) = trace {
            let role_tag = if t.role == "reflector" { "reflector" } else { "task-agent" };
            // Session groups task-agent + its following reflector under
            // the same trace when callers build matching session ids.
            // Kept stable by caller: `<task>-n<NNN>-<role>`.
            let session_id = format!(
                "{}-n{:03}-{}",
                t.task_id,
                t.run_number,
                role_tag
            );
            body["session_id"] = serde_json::Value::String(session_id);

            let env_label = if t.environment.is_empty() {
                std::env::var("OPENROUTER_TRACE_ENV").unwrap_or_else(|_| "default".into())
            } else {
                t.environment.clone()
            };
            body["trace"] = serde_json::json!({
                "trace_name": role_tag,
                "generation_name": t.task_id,
                "environment": env_label,
            });
        } else if let Ok(env_label) = std::env::var("OPENROUTER_TRACE_ENV") {
            // Still ship the environment even when we don't have a full
            // context — useful for ad-hoc CLI runs.
            body["trace"] = serde_json::json!({ "environment": env_label });
        }

        if !options.tool_definitions.is_empty() {
            let tools: Vec<serde_json::Value> = options
                .tool_definitions
                .iter()
                .map(|td| {
                    serde_json::json!({
                        "type": "function",
                        "function": {
                            "name": td.name,
                            "description": td.description,
                            "parameters": td.parameters,
                        }
                    })
                })
                .collect();
            body["tools"] = serde_json::Value::Array(tools);
        }

        match &options.response_format {
            ResponseFormat::Text => {}
            ResponseFormat::JsonObject => {
                body["response_format"] = serde_json::json!({"type": "json_object"});
            }
            ResponseFormat::JsonSchema { schema, name } => {
                body["response_format"] = serde_json::json!({
                    "type": "json_schema",
                    "json_schema": {
                        "name": name.as_deref().unwrap_or("response"),
                        "schema": schema,
                    }
                });
            }
        }

        body
    }
}

#[async_trait]
impl InferenceProvider for OpenRouterInferenceProvider {
    async fn complete(
        &self,
        conversation: &Conversation,
        options: &InferenceOptions,
    ) -> Result<InferenceResponse, InferenceError> {
        let trace_snapshot = self.trace.lock().await.clone();
        let body = self.build_body(conversation, options, trace_snapshot.as_ref());
        let url = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));

        let t0 = std::time::Instant::now();
        let mut req = self
            .http
            .post(&url)
            .header("authorization", format!("Bearer {}", self.api_key))
            .header("content-type", "application/json")
            // Referer + X-Title help OpenRouter's dashboard group spend
            // by caller; nothing the demo relies on.
            .header("HTTP-Referer", "https://github.com/ThirdKeyAI/symbiont-orga-demo")
            .header("X-Title", "symbiont-orga-demo");
        // Also send session id as a header — OpenRouter accepts it in
        // either place, and headers survive request rewriting that
        // upstream providers sometimes do.
        if let Some(t) = &trace_snapshot {
            let role_tag = if t.role == "reflector" { "reflector" } else { "task-agent" };
            req = req.header(
                "x-session-id",
                format!("{}-n{:03}-{}", t.task_id, t.run_number, role_tag),
            );
        }
        let resp = req
            .json(&body)
            .send()
            .await
            .map_err(|e| {
                if e.is_timeout() {
                    InferenceError::Timeout(std::time::Duration::from_secs(600))
                } else {
                    InferenceError::Provider(format!("request failed: {e}"))
                }
            })?;

        let status = resp.status();
        if status.as_u16() == 429 {
            let retry_after = resp
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(1);
            return Err(InferenceError::RateLimited {
                retry_after_ms: retry_after * 1000,
            });
        }
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_else(|_| "<no body>".into());
            return Err(InferenceError::Provider(format!(
                "openrouter API error ({status}): {text}"
            )));
        }

        let json: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| InferenceError::ParseError(format!("json parse: {e}")))?;
        let latency_ms = t0.elapsed().as_millis() as u64;

        let parsed = parse_openai_response(&json, &self.model)?;

        // Capture the OpenRouter-only fields into the call log.
        let generation_id = json
            .get("id")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        let upstream_provider = json
            .get("provider")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        let cost_usd = json
            .get("usage")
            .and_then(|u| u.get("cost"))
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let finish_reason_str = json
            .pointer("/choices/0/finish_reason")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let log = CallLog {
            completed_at_ms: chrono::Utc::now().timestamp_millis(),
            generation_id,
            upstream_provider,
            model: parsed.model.clone(),
            cost_usd,
            prompt_tokens: parsed.usage.prompt_tokens,
            completion_tokens: parsed.usage.completion_tokens,
            total_tokens: parsed.usage.total_tokens,
            latency_ms,
            tool_calls_emitted: parsed.tool_calls.len() as u32,
            finish_reason: finish_reason_str,
            http_status: status.as_u16(),
        };
        self.calls.lock().await.push(log);

        Ok(parsed)
    }

    fn provider_name(&self) -> &str {
        "openrouter"
    }

    fn default_model(&self) -> &str {
        &self.model
    }

    fn supports_native_tools(&self) -> bool {
        true
    }

    fn supports_structured_output(&self) -> bool {
        true
    }
}

fn parse_openai_response(
    resp: &serde_json::Value,
    fallback_model: &str,
) -> Result<InferenceResponse, InferenceError> {
    let choice = resp
        .get("choices")
        .and_then(|c| c.get(0))
        .ok_or_else(|| InferenceError::ParseError("no choices in response".into()))?;
    let message = choice
        .get("message")
        .ok_or_else(|| InferenceError::ParseError("no message in choice".into()))?;

    let content = message
        .get("content")
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string();

    let tool_calls = message
        .get("tool_calls")
        .and_then(|tc| tc.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|tc| {
                    let id = tc
                        .get("id")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| format!("call_{}", uuid::Uuid::new_v4()));
                    let func = tc.get("function")?;
                    let name = func.get("name")?.as_str()?.to_string();
                    let args = match func.get("arguments") {
                        Some(v) if v.is_string() => v.as_str().unwrap().to_string(),
                        Some(v) => v.to_string(),
                        None => "{}".to_string(),
                    };
                    Some(ToolCallRequest {
                        id,
                        name,
                        arguments: args,
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let finish_reason = match choice
        .get("finish_reason")
        .and_then(|f| f.as_str())
        .unwrap_or("stop")
    {
        "tool_calls" => FinishReason::ToolCalls,
        "length" => FinishReason::MaxTokens,
        "content_filter" => FinishReason::ContentFilter,
        _ => {
            if tool_calls.is_empty() {
                FinishReason::Stop
            } else {
                FinishReason::ToolCalls
            }
        }
    };

    let usage = resp
        .get("usage")
        .map(|u| Usage {
            prompt_tokens: u.get("prompt_tokens").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
            completion_tokens: u
                .get("completion_tokens")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            total_tokens: u.get("total_tokens").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
        })
        .unwrap_or_default();

    let model = resp
        .get("model")
        .and_then(|m| m.as_str())
        .unwrap_or(fallback_model)
        .to_string();

    Ok(InferenceResponse {
        content,
        tool_calls,
        finish_reason,
        usage,
        model,
    })
}
