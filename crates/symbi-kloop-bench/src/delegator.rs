//! Delegator pass — picks which task the worker should run next.
//!
//! v8 #3 — turns the v6 `DelegatorActionExecutor` from a structural
//! proof (three unit tests of principal isolation) into a behavioural
//! component visible in the runs table and on the dashboard.
//!
//! Wired through the `Demo` subcommand's `--with-delegator` flag.
//! When set, each iteration of the demo loop runs:
//!
//!   1. Delegator picks a `task_id` from the loaded set via its
//!      single permitted tool, `choose_task`.
//!   2. Task agent runs that task as usual.
//!   3. Reflector reflects on the run as usual.
//!
//! The delegator agent uses:
//!   - `Agent::"delegator"` Cedar principal
//!     (`policies/delegator.cedar`).
//!   - `DelegatorActionExecutor` with profile-of-one `choose_task`.
//!   - The same provider configuration as the task agent (so the
//!     three-principal claim is end-to-end on a real model).
//!
//! If the LLM emits a `choose_task` with an unknown task id, or
//! tries any other tool, the executor refuses, the run is recorded
//! with `kind='delegate'`, and the harness either retries the
//! delegator or falls back to a deterministic round-robin.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use demo_karpathy_loop::{openrouter_provider::TraceContext, DelegatorActionExecutor};
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::context_manager::DefaultContextManager;
use symbi_runtime::reasoning::conversation::{Conversation, ConversationMessage};
use symbi_runtime::reasoning::inference::InferenceProvider;
use symbi_runtime::reasoning::loop_types::{BufferedJournal, LoopConfig, LoopResult};
use symbi_runtime::reasoning::policy_bridge::ReasoningPolicyGate;
use symbi_runtime::reasoning::reasoning_loop::ReasoningLoopRunner;
use symbi_runtime::types::AgentId;

use crate::db::RunKind;
use crate::harness::Ctx;
use crate::policy_gate::NamedPrincipalCedarGate;

/// Run one delegator pass over the given allowed task ids; record a
/// `kind='delegate'` row; return the chosen task id (or `None` if
/// the LLM didn't emit a usable `choose_task`).
pub async fn run_delegator(
    ctx: &Ctx,
    allowed_task_ids: &[String],
    iteration: u32,
) -> Result<Option<String>> {
    let agent_id = AgentId::new();

    let executor = Arc::new(DelegatorActionExecutor::new(
        allowed_task_ids.to_vec(),
    ));

    // v12.1 — branch on cedar_mode for ablation.
    let (cedar_denied, gate_calls, gate_ns_total, gate_ns_max, gate) = {
        if ctx.cedar_mode.is_active() {
            let cedar = NamedPrincipalCedarGate::from_file(
                "delegator",
                &ctx.policies_dir.join("delegator.cedar"),
            )
            .with_context(|| "load delegator.cedar")?;
            let denied = cedar.denied_counter();
            let (calls, ns_total, ns_max) = cedar.latency_counters();
            let g: Arc<dyn ReasoningPolicyGate> = Arc::new(cedar);
            (denied, calls, ns_total, ns_max, g)
        } else {
            tracing::warn!(
                "v12.1 ablation: delegator --cedar-mode off — gate is permissive stub"
            );
            let p = crate::policy_gate::PermissiveGate::new();
            let denied = p.denied_counter();
            let (calls, ns_total, ns_max) = p.latency_counters();
            let g: Arc<dyn ReasoningPolicyGate> = Arc::new(p);
            (denied, calls, ns_total, ns_max, g)
        }
    };

    let journal = Arc::new(BufferedJournal::new(512));
    let or_handle = ctx.fresh_openrouter_task_provider();
    if let Some(h) = &or_handle {
        h.set_trace_context(TraceContext {
            task_id: "delegator".into(),
            run_number: iteration,
            role: "delegator".into(),
            environment: std::env::var("OPENROUTER_TRACE_ENV").unwrap_or_default(),
        })
        .await;
    }
    let provider_arc: Arc<dyn InferenceProvider> = match &or_handle {
        Some(h) => h.clone(),
        None => ctx.fresh_provider(),
    };
    let runner = ReasoningLoopRunner::builder()
        .provider(provider_arc)
        .executor(executor.clone())
        .policy_gate(gate)
        .context_manager(Arc::new(DefaultContextManager::default()))
        .circuit_breakers(Arc::new(CircuitBreakerRegistry::default()))
        .journal(journal.clone())
        .build();

    let system = "You are the DELEGATOR agent in the Symbiont Karpathy-loop \
                  demo. Your ONLY tool is `choose_task`. Pick the next \
                  task to run from the supplied list and call \
                  `choose_task({\"task_id\": \"<id>\"})` exactly once.";
    let mut conv = Conversation::with_system(system);
    let user = format!(
        "iteration={iter}\n\
         Available task ids (pick one):\n  {ids}\n\
         \n\
         Pick a task id and call `choose_task` with it. Round-robin \
         is acceptable; stay focused on tasks that have not run yet \
         this iteration when possible.",
        iter = iteration,
        ids = allowed_task_ids.join("\n  ")
    );
    conv.push(ConversationMessage::user(&user));

    let config = LoopConfig {
        max_iterations: 3,
        max_total_tokens: 2_000,
        timeout: Duration::from_secs(30),
        tool_definitions: vec![DelegatorActionExecutor::tool_definition()],
        temperature: ctx.temperature,
        ..Default::default()
    };

    let started_at = Utc::now();
    let result: LoopResult = runner.run(agent_id, conv, config).await;
    let completed_at = Utc::now();

    // Persist journal (sanitised by harness::write_journal_file's
    // pipeline).
    let entries = journal.entries().await;
    let journal_path = ctx
        .write_named_journal("delegator", iteration, "delegate", &entries)?;

    // OpenRouter call sidecar + cost capture.
    let mut authoritative_cost: Option<f64> = None;
    if let Some(h) = &or_handle {
        let calls = h.drain_calls().await;
        if !calls.is_empty() {
            let sum: f64 = calls.iter().map(|c| c.cost_usd).sum();
            if sum > 0.0 {
                authoritative_cost = Some(sum);
            }
            let _ = ctx.write_calls_sidecar("delegator", iteration, "delegate", &calls);
        }
    }

    let chosen = executor.chosen().await;
    let executor_n = executor.refused_count().await;
    let cedar_n = cedar_denied.load(std::sync::atomic::Ordering::Relaxed);
    let violations = cedar_n + executor_n;

    let prompt_tokens = result.total_usage.prompt_tokens;
    let completion_tokens = result.total_usage.completion_tokens;
    let (pt, ct) = if prompt_tokens == 0 && completion_tokens == 0
        && result.total_usage.total_tokens > 0
    {
        crate::pricing::split_70_30(result.total_usage.total_tokens)
    } else {
        (prompt_tokens, completion_tokens)
    };
    let task_pricing_key = ctx.pricing_key_for("task");
    let est_cost = authoritative_cost
        .unwrap_or_else(|| crate::pricing::cost_usd(&task_pricing_key, pt, ct));

    let chosen_label = chosen.clone().unwrap_or_else(|| "(none)".into());
    ctx.db
        .record_run(
            &chosen_label,
            iteration,
            RunKind::Delegate,
            started_at,
            completed_at,
            // Score for a delegator pass is "did it pick something" —
            // 1.0 if a task id was committed, else 0.0.
            if chosen.is_some() { 1.0 } else { 0.0 },
            result.iterations,
            result.total_usage.total_tokens,
            journal_path.as_deref(),
            &describe_termination(&result.termination_reason),
            violations,
            &task_pricing_key,
            est_cost,
            pt,
            ct,
            cedar_n,
            executor_n,
            gate_calls.load(std::sync::atomic::Ordering::Relaxed),
            gate_ns_total.load(std::sync::atomic::Ordering::Relaxed),
            gate_ns_max.load(std::sync::atomic::Ordering::Relaxed),
        )
        .await?;

    Ok(chosen)
}

fn describe_termination(reason: &symbi_runtime::reasoning::loop_types::TerminationReason) -> String {
    use symbi_runtime::reasoning::loop_types::TerminationReason as T;
    match reason {
        T::Completed => "completed".into(),
        T::MaxIterations => "max_iterations".into(),
        T::MaxTokens => "max_tokens".into(),
        T::Timeout => "timeout".into(),
        T::PolicyDenial { reason } => format!("policy_denial: {reason}"),
        T::Error { message } => format!("error: {message}"),
    }
}
