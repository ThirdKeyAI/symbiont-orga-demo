//! `OllamaInferenceProvider` — OpenAI-compatible client for local Ollama.
//!
//! The shared `CloudInferenceProvider` routes through `LlmClient::from_env()`,
//! which validates every base URL against `net_guard::reject_ssrf_url` and
//! rejects RFC1918 / loopback addresses outright. That SSRF guard is the
//! right default for production, but it blocks the demo's "point at a LAN
//! box running Ollama" use case. Rather than punch a hole in the shared
//! guard, we ship a dedicated provider here that talks directly to an
//! operator-chosen Ollama endpoint via `reqwest`. Scope is limited to the
//! bench binary, so the general-purpose guard stays intact.
//!
//! Wire format: the OpenAI-compatible chat completions endpoint Ollama
//! exposes at `${base}/chat/completions` (OpenAI v1 shape). Tool calling is
//! supported by Ollama for models that advertise it (gemma3, qwen3, etc.).

use async_trait::async_trait;
use symbi_runtime::reasoning::conversation::Conversation;
use symbi_runtime::reasoning::inference::{
    FinishReason, InferenceError, InferenceOptions, InferenceProvider, InferenceResponse,
    ResponseFormat, ToolCallRequest, Usage,
};

pub struct OllamaInferenceProvider {
    base_url: String,
    model: String,
    http: reqwest::Client,
}

impl OllamaInferenceProvider {
    /// `base_url` should be the OpenAI-compat root, e.g.
    /// `http://localhost:11434/v1` or `http://<host>:11434/v1` for a
    /// LAN Ollama. `model` is the tag Ollama knows (see
    /// `GET /api/tags`) — e.g. `gemma4:latest`.
    pub fn new(base_url: impl Into<String>, model: impl Into<String>) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()
            .expect("reqwest client");
        Self {
            base_url: base_url.into(),
            model: model.into(),
            http,
        }
    }

    fn build_body(
        &self,
        conversation: &Conversation,
        options: &InferenceOptions,
    ) -> serde_json::Value {
        let model = options.model.as_deref().unwrap_or(&self.model);

        let mut body = serde_json::json!({
            "model": model,
            "messages": conversation.to_openai_messages(),
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
        });

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
impl InferenceProvider for OllamaInferenceProvider {
    async fn complete(
        &self,
        conversation: &Conversation,
        options: &InferenceOptions,
    ) -> Result<InferenceResponse, InferenceError> {
        let body = self.build_body(conversation, options);
        let url = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));

        let resp = self
            .http
            .post(&url)
            .header("content-type", "application/json")
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
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_else(|_| "<no body>".into());
            return Err(InferenceError::Provider(format!(
                "ollama API error ({status}): {text}"
            )));
        }

        let json: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| InferenceError::ParseError(format!("json parse: {e}")))?;

        parse_openai_response(&json, &self.model)
    }

    fn provider_name(&self) -> &str {
        "ollama"
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
                    // Ollama emits `arguments` as a JSON value sometimes
                    // (object) and sometimes as a string. Handle both.
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
