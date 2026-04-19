//! Reflector pass — runs AFTER the task agent completes and proposes
//! 0–5 procedures for the next run.
//!
//! The reflector:
//!
//! - Uses a separate `AgentId` so Cedar decisions are attributable.
//! - Runs with `ReflectorActionExecutor` (tool profile of exactly one:
//!   `store_knowledge`).
//! - Runs with a `NamedPrincipalCedarGate` keyed on `reflector`,
//!   loaded from `policies/reflector.cedar`.
//! - Sees the just-completed run's task id, score, answer, iteration
//!   count, and the tool-result observations from its journal.
//!
//! The harness records a `runs` row for the reflector pass with
//! `kind='reflect'` and `violations_prevented` populated from the
//! reflector executor's refusal counter. That number is the demo's
//! proof artifact.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use demo_karpathy_loop::{ReflectorActionExecutor, Task};
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::context_manager::DefaultContextManager;
use symbi_runtime::reasoning::conversation::{Conversation, ConversationMessage};
use symbi_runtime::reasoning::loop_types::{BufferedJournal, LoopConfig, LoopResult};
use symbi_runtime::reasoning::policy_bridge::ReasoningPolicyGate;
use symbi_runtime::reasoning::reasoning_loop::ReasoningLoopRunner;
use symbi_runtime::types::AgentId;

use crate::db::RunKind;
use crate::harness::{Ctx, IterationResult};
use crate::policy_gate::NamedPrincipalCedarGate;

pub async fn run_reflector(
    ctx: &Ctx,
    task: &Task,
    task_result: &IterationResult,
    learned_at_run_id: i64,
) -> Result<()> {
    let agent_id = AgentId::new();

    let executor = Arc::new(ReflectorActionExecutor::new(
        &task.id,
        Some(learned_at_run_id),
        ctx.knowledge.clone(),
    ));

    let cedar = NamedPrincipalCedarGate::from_file(
        "reflector",
        &ctx.policies_dir.join("reflector.cedar"),
    )
    .with_context(|| "load reflector.cedar")?;
    // Capture the denied counter before handing Cedar off into the trait
    // object. The gate's Arc is consumed by the runner, so we rely on the
    // counter's own `Arc<AtomicU32>` to read it back after the run.
    let cedar_denied = cedar.denied_counter();
    let gate: Arc<dyn ReasoningPolicyGate> = Arc::new(cedar);

    let journal = Arc::new(BufferedJournal::new(1_024));
    let runner = ReasoningLoopRunner::builder()
        .provider(ctx.fresh_provider())
        .executor(executor.clone())
        .policy_gate(gate)
        .context_manager(Arc::new(DefaultContextManager::default()))
        .circuit_breakers(Arc::new(CircuitBreakerRegistry::default()))
        .journal(journal.clone())
        .build();

    let system = "You are the REFLECTOR agent for the Symbiont Karpathy-loop demo. \
                  Your ONLY tool is store_knowledge. After reading the run summary \
                  below, record between zero and five concrete procedures the task \
                  agent should remember for similar future tasks. Procedures must be \
                  in subject-predicate-object form, each field under 60 characters. \
                  Do NOT invoke any other tool.";
    let mut conv = Conversation::with_system(system);

    // Feed the reflector the task agent's run summary. The `task_id=` line
    // is what `MockInferenceProvider` keys on; it also makes the summary
    // easy to grep in the reflector's journal.
    let user = format!(
        "task_id={id}\n\
         Run summary for {id} attempt #{n}:\n\
         - score: {score:.2}\n\
         - iterations: {iters}\n\
         - tokens: {tokens}\n\
         - final answer: {answer}\n\
         - termination: {term}\n\
         \n\
         Propose 0–5 procedures the next run of this task should remember.",
        id = task.id,
        n = task_result.run_number,
        score = task_result.score,
        iters = task_result.iterations,
        tokens = task_result.total_tokens,
        answer = task_result.answer.as_deref().unwrap_or("(none)"),
        term = task_result.termination,
    );
    conv.push(ConversationMessage::user(&user));

    let config = LoopConfig {
        max_iterations: 10,
        max_total_tokens: 20_000,
        timeout: Duration::from_secs(60),
        tool_definitions: vec![ReflectorActionExecutor::tool_definition()],
        ..Default::default()
    };

    let started_at = Utc::now();
    let result: LoopResult = runner.run(agent_id, conv, config).await;
    let completed_at = Utc::now();

    // Persist journal.
    let entries = journal.entries().await;
    let journal_path = write_reflector_journal(ctx, task, task_result.run_number, &entries)?;

    let stored = executor.stored_count().await;
    // "Violations prevented" = Cedar denials + any executor-layer refusals.
    // In practice the executor counter stays at 0 because Cedar sits
    // in front of it, but summing makes the number robust to a future
    // change that loosens Cedar without loosening the executor (or
    // vice versa).
    let violations = cedar_denied.load(std::sync::atomic::Ordering::Relaxed)
        + executor.refused_count().await;

    ctx.db
        .record_run(
            &task.id,
            task_result.run_number,
            RunKind::Reflect,
            started_at,
            completed_at,
            // "score" for a reflector run isn't really a score — we
            // repurpose it as the count of procedures stored this pass
            // so the dashboard can render both task and reflector rows
            // under a single column without special-casing.
            stored as f64,
            result.iterations,
            result.total_usage.total_tokens,
            journal_path.as_deref(),
            &describe_termination(&result.termination_reason),
            violations,
        )
        .await?;

    Ok(())
}

fn write_reflector_journal(
    ctx: &Ctx,
    task: &Task,
    run_number: u32,
    entries: &[symbi_runtime::reasoning::loop_types::JournalEntry],
) -> Result<Option<String>> {
    std::fs::create_dir_all(&ctx.journals_dir).ok();
    let fname = format!(
        "{}-{}-n{:03}-reflect.json",
        chrono::Utc::now().format("%Y%m%d-%H%M%S"),
        task.id,
        run_number
    );
    let path = ctx.journals_dir.join(fname);
    std::fs::write(&path, serde_json::to_string_pretty(entries)?)?;
    Ok(Some(path.display().to_string()))
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
