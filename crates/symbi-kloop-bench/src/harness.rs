//! Task harness — runs one ORGA iteration against a task, then drives
//! the reflector against the result.
//!
//! `Ctx::bootstrap` loads tasks, policies, opens DBs, builds the provider
//! once. `Ctx::run_iteration` runs a single (task, reflector) pair and
//! persists everything. `Ctx::run_demo` is the outer orchestrator (WI-4).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use demo_karpathy_loop::{
    provider::{MockInferenceProvider, TaskScript},
    KnowledgeStore, Task, TaskActionExecutor,
};
use symbi_runtime::reasoning::circuit_breaker::CircuitBreakerRegistry;
use symbi_runtime::reasoning::context_manager::DefaultContextManager;
use symbi_runtime::reasoning::conversation::{Conversation, ConversationMessage};
use symbi_runtime::reasoning::inference::InferenceProvider;
use symbi_runtime::reasoning::loop_types::{
    BufferedJournal, JournalEntry, LoopConfig, LoopResult, TerminationReason,
};
use symbi_runtime::reasoning::policy_bridge::ReasoningPolicyGate;
use symbi_runtime::reasoning::reasoning_loop::ReasoningLoopRunner;
use symbi_runtime::types::AgentId;

use crate::db::{Db, RunKind};
use crate::policy_gate::NamedPrincipalCedarGate;
use crate::reflector;
use crate::task_tools;
use crate::Provider;

pub struct CtxConfig {
    pub db_path: PathBuf,
    pub tasks_dir: PathBuf,
    pub policies_dir: PathBuf,
    pub journals_dir: PathBuf,
    pub provider: Provider,
}

/// Shared across subcommand invocations.
pub struct Ctx {
    pub db: Db,
    pub knowledge: KnowledgeStore,
    pub tasks: HashMap<String, Task>,
    pub journals_dir: PathBuf,
    pub policies_dir: PathBuf,
    /// How to obtain an inference provider for each run.
    ///
    /// The mock variant holds a script bundle and builds a fresh
    /// `MockInferenceProvider` per `fresh_provider()` call so cursor
    /// state doesn't carry between iterations. The cloud variant holds
    /// an `Arc<dyn InferenceProvider>` that's reused — real providers
    /// are stateless w.r.t. our script cursors so there's nothing to
    /// reset.
    provider_source: ProviderSource,
}

enum ProviderSource {
    Mock {
        scripts: HashMap<String, TaskScript>,
    },
    Cloud {
        provider: Arc<dyn InferenceProvider>,
    },
}

impl Ctx {
    pub async fn bootstrap(cfg: CtxConfig) -> Result<Self> {
        let db = Db::open(&cfg.db_path)?;
        let knowledge_path = cfg
            .db_path
            .parent()
            .unwrap_or_else(|| std::path::Path::new("."))
            .join("knowledge.db");
        let knowledge = KnowledgeStore::open(&knowledge_path)?;

        let tasks = load_tasks(&cfg.tasks_dir)?;

        let provider_source = match cfg.provider {
            Provider::Mock => ProviderSource::Mock {
                scripts: crate::mock_scripts::bundle(),
            },
            Provider::Cloud => {
                let p = symbi_runtime::reasoning::providers::cloud::CloudInferenceProvider::from_env()
                    .ok_or_else(|| anyhow::anyhow!(
                        "--provider cloud but no API key in env (OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY)"
                    ))?;
                ProviderSource::Cloud {
                    provider: Arc::new(p),
                }
            }
        };

        Ok(Self {
            db,
            knowledge,
            tasks,
            journals_dir: cfg.journals_dir,
            policies_dir: cfg.policies_dir,
            provider_source,
        })
    }

    /// Mint an inference provider for a single run.
    ///
    /// Mock: builds a fresh `MockInferenceProvider` from the cached
    /// script bundle so cursor state starts at 0 for every run. Cloud:
    /// returns a clone of the shared `Arc`.
    pub fn fresh_provider(&self) -> Arc<dyn InferenceProvider> {
        match &self.provider_source {
            ProviderSource::Mock { scripts } => {
                MockInferenceProvider::with_scripts(scripts.clone())
            }
            ProviderSource::Cloud { provider } => provider.clone(),
        }
    }

    /// Look up a task by id, returning a clone.
    fn task(&self, task_id: &str) -> Result<Task> {
        self.tasks
            .get(task_id)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("unknown task id '{task_id}'"))
    }

    /// Run one iteration of a task (task agent then reflector).
    pub async fn run_iteration(&self, task_id: &str, n: u32) -> Result<IterationResult> {
        self.run_iteration_with(task_id, n, reflector::ReflectorPrompt::Default)
            .await
    }

    /// Same as `run_iteration` but lets the caller choose which
    /// reflector system prompt to use. The adversarial variant is for
    /// stress-testing Cedar on cloud runs.
    pub async fn run_iteration_with(
        &self,
        task_id: &str,
        n: u32,
        prompt: reflector::ReflectorPrompt,
    ) -> Result<IterationResult> {
        let task = self.task(task_id)?;

        let task_outcome = self
            .run_task_agent(&task, n)
            .await
            .with_context(|| format!("task agent run for {task_id} #{n}"))?;

        // Reflector only runs when the task agent produced *something* to
        // reflect on. If the task run aborted with no iterations, skip.
        if task_outcome.iterations == 0 {
            tracing::warn!("task agent produced no iterations; skipping reflector");
        } else if let Err(e) =
            reflector::run_reflector(self, &task, &task_outcome, task_outcome.run_id, prompt).await
        {
            tracing::error!(task = %task_id, error = %e, "reflector pass failed");
        }

        Ok(task_outcome)
    }

    /// Run the full demo. Convenience wrapper around
    /// `run_demo_filtered(iterations, None, false)`.
    #[allow(dead_code)]
    pub async fn run_demo(&self, iterations: u32) -> Result<DemoSummary> {
        self.run_demo_filtered(iterations, None, false).await
    }

    /// Run the demo with optional task filter and adversarial-reflector
    /// flag. `only = Some("T4")` restricts to a single task id; `None`
    /// means every loaded task in sorted order. `adversarial` swaps in
    /// the reflector prompt that tempts the LLM to call forbidden tools.
    pub async fn run_demo_filtered(
        &self,
        iterations: u32,
        only: Option<&str>,
        adversarial: bool,
    ) -> Result<DemoSummary> {
        let mut task_ids: Vec<String> =
            self.tasks.keys().cloned().collect();
        task_ids.sort();
        if let Some(target) = only {
            task_ids.retain(|id| id == target);
            if task_ids.is_empty() {
                anyhow::bail!("--only '{}' matched no loaded task", target);
            }
        }
        let task_count = task_ids.len();
        let prompt = if adversarial {
            reflector::ReflectorPrompt::Adversarial
        } else {
            reflector::ReflectorPrompt::Default
        };

        let mut total_runs = 0u32;
        for n in 1..=iterations {
            for id in &task_ids {
                match self.run_iteration_with(id, n, prompt).await {
                    Ok(_) => total_runs += 1,
                    Err(e) => tracing::error!(task = %id, error = %e, "iteration failed"),
                }
            }
        }

        let stored_procedures = self.knowledge.total().await.unwrap_or(0);
        let violations = self.db.total_violations_prevented().await.unwrap_or(0);

        Ok(DemoSummary {
            task_count,
            total_runs,
            stored_procedures,
            violations_prevented: violations,
        })
    }

    /// Build a fresh `ReasoningLoopRunner` and run the task agent for one
    /// ORGA pass. Persists the journal, writes a `runs` row, returns the
    /// summary the reflector (and caller) want.
    async fn run_task_agent(&self, task: &Task, n: u32) -> Result<IterationResult> {
        // Per-run agent id — Cedar matching is done by our named gate so
        // this UUID is just for correlation in the journal.
        let agent_id = AgentId::new();

        // Build executor + tool defs specific to the task.
        let mut executor = TaskActionExecutor::new(&task.id, self.knowledge.clone());
        let task_tool_defs = task_tools::register_for_task(task, &mut executor)?;
        let executor = Arc::new(executor);

        // Cedar gate for the task agent.
        let gate: Arc<dyn ReasoningPolicyGate> = Arc::new(
            NamedPrincipalCedarGate::from_file(
                "task_agent",
                &self.policies_dir.join("task-agent.cedar"),
            )
            .with_context(|| "load task-agent.cedar")?,
        );

        let journal = Arc::new(BufferedJournal::new(4_096));
        let runner = ReasoningLoopRunner::builder()
            .provider(self.fresh_provider())
            .executor(executor.clone())
            .policy_gate(gate)
            .context_manager(Arc::new(DefaultContextManager::default()))
            .circuit_breakers(Arc::new(CircuitBreakerRegistry::default()))
            .journal(journal.clone())
            .build();

        // Conversation: system prompt (with the learned-marker hook for
        // the mock provider), then user prompt.
        let system = format!(
            "You are the TASK AGENT for the Symbiont Karpathy-loop demo. \
             Follow the DSL governance rules from agents/task-agent.dsl. \
             Task id: {task_id}. Call recall_knowledge first and apply \
             any learned procedures before choosing an approach.",
            task_id = task.id
        );
        let mut conv = Conversation::with_system(&system);
        conv.push(ConversationMessage::user(&task.prompt));

        // Tight loop budget so the mock provider's long path still
        // terminates; real runs can bump this via env var later.
        let mut all_tool_defs = TaskActionExecutor::builtin_definitions();
        all_tool_defs.extend(task_tool_defs);
        let config = LoopConfig {
            max_iterations: 20,
            max_total_tokens: 50_000,
            timeout: Duration::from_secs(120),
            tool_definitions: all_tool_defs,
            ..Default::default()
        };

        let started_at = Utc::now();
        let result: LoopResult = runner.run(agent_id, conv, config).await;
        let completed_at = Utc::now();

        // Persist the journal to disk for replay.
        let entries = journal.entries().await;
        let journal_path = self.write_journal_file(&task.id, n, "task", &entries)?;

        // Build the tool-trace the reflector will read from the task
        // agent's final conversation. One line per tool call, followed
        // by a trimmed version of the observation the tool returned.
        // Bounded to ~20 entries so the reflector prompt stays small
        // even if the task agent looped longer.
        let tool_trace = summarise_tool_trace(&result.conversation, 20);

        // Score the agent's answer.
        let answer = executor.outcome().await;
        let outcome = task.grade(answer.as_deref());

        let termination = describe_termination(&result.termination_reason);

        let run_id = self
            .db
            .record_run(
                &task.id,
                n,
                RunKind::Task,
                started_at,
                completed_at,
                outcome.score,
                result.iterations,
                result.total_usage.total_tokens,
                journal_path.as_deref(),
                &termination,
                0,
            )
            .await?;

        Ok(IterationResult {
            run_id,
            task_id: task.id.clone(),
            run_number: n,
            score: outcome.score,
            iterations: result.iterations,
            total_tokens: result.total_usage.total_tokens,
            termination,
            answer,
            journal_path,
            tool_trace,
        })
    }

    /// Write a signed journal to disk. We serialize to JSON for now —
    /// Symbiont's own signed-journal format is a superset we can adopt
    /// later without changing the filename layout the dashboard reads.
    fn write_journal_file(
        &self,
        task_id: &str,
        run_number: u32,
        tag: &str,
        entries: &[JournalEntry],
    ) -> Result<Option<String>> {
        std::fs::create_dir_all(&self.journals_dir).ok();
        let fname = format!(
            "{}-{}-n{:03}-{}.json",
            chrono::Utc::now().format("%Y%m%d-%H%M%S"),
            task_id,
            run_number,
            tag
        );
        let path = self.journals_dir.join(fname);
        let text = serde_json::to_string_pretty(entries)?;
        std::fs::write(&path, text)?;
        Ok(Some(path.display().to_string()))
    }
}

/// Result of one task agent iteration.
#[derive(Debug, Clone)]
pub struct IterationResult {
    pub run_id: i64,
    pub task_id: String,
    pub run_number: u32,
    pub score: f64,
    pub iterations: u32,
    pub total_tokens: u32,
    pub termination: String,
    pub answer: Option<String>,
    /// Where the task agent's journal was written on disk, if any. Read
    /// by the report generator when it wants to cite specific runs.
    #[allow(dead_code)]
    pub journal_path: Option<String>,
    /// A compact one-line-per-step replay of what the task agent did,
    /// extracted from the in-memory journal before it went to disk.
    ///
    /// The reflector reads this to decide which probe was decisive and
    /// which were wasted — without it the reflector only sees
    /// score/tokens/iterations and has no way to propose an actionable
    /// procedure. Bounded on the task side to ~20 entries so the
    /// reflector prompt stays small even if the loop ran longer.
    pub tool_trace: String,
}

/// Final summary returned by the demo orchestrator.
#[derive(Debug, Clone)]
pub struct DemoSummary {
    pub task_count: usize,
    pub total_runs: u32,
    pub stored_procedures: i64,
    pub violations_prevented: i64,
}

fn load_tasks(dir: &std::path::Path) -> Result<HashMap<String, Task>> {
    let mut out = HashMap::new();
    for task in Task::load_dir(dir)? {
        out.insert(task.id.clone(), task);
    }
    if out.is_empty() {
        tracing::warn!(dir = %dir.display(), "no tasks loaded — demo will do nothing");
    }
    Ok(out)
}

fn describe_termination(reason: &TerminationReason) -> String {
    match reason {
        TerminationReason::Completed => "completed".into(),
        TerminationReason::MaxIterations => "max_iterations".into(),
        TerminationReason::MaxTokens => "max_tokens".into(),
        TerminationReason::Timeout => "timeout".into(),
        TerminationReason::PolicyDenial { reason } => format!("policy_denial: {reason}"),
        TerminationReason::Error { message } => format!("error: {message}"),
    }
}

/// Compact human-readable trace of the task agent's tool-calling
/// behaviour, built from the final conversation history.
///
/// Format: one line per tool call, showing call index, tool name, and a
/// trimmed view of the observation the tool returned. Truncated to
/// `max_calls` entries so the reflector's prompt budget stays small
/// even for long runs.
fn summarise_tool_trace(
    conversation: &symbi_runtime::reasoning::conversation::Conversation,
    max_calls: usize,
) -> String {
    use std::collections::HashMap;
    use symbi_runtime::reasoning::conversation::MessageRole;

    // Build `call_id -> (tool_name, args_preview)` from assistant tool
    // calls, then for each tool-role message emit a line with the tool
    // name and the observation content.
    let mut pending: HashMap<String, (String, String)> = HashMap::new();
    let mut lines: Vec<String> = Vec::new();
    let mut idx = 0usize;

    for msg in conversation.messages() {
        match msg.role {
            MessageRole::Assistant => {
                for tc in &msg.tool_calls {
                    let preview = trim_to(&tc.arguments, 80).replace(['\n', '\r'], " ");
                    pending.insert(tc.id.clone(), (tc.name.clone(), preview));
                }
            }
            MessageRole::Tool => {
                let call_id = msg.tool_call_id.as_deref().unwrap_or("");
                let (name, args_preview) = pending.remove(call_id).unwrap_or_else(|| {
                    (
                        msg.tool_name.clone().unwrap_or_else(|| "?".into()),
                        String::new(),
                    )
                });
                idx += 1;
                let obs_preview = trim_to(&msg.content, 160).replace(['\n', '\r'], " ⏎ ");
                let args_hint = if args_preview.is_empty() {
                    String::new()
                } else {
                    format!(" {args_preview}")
                };
                lines.push(format!(
                    "{idx:>2}. {name}{args_hint} -> {obs_preview}"
                ));
                if lines.len() >= max_calls {
                    break;
                }
            }
            _ => {}
        }
    }

    if lines.is_empty() {
        "(no tool calls recorded)".into()
    } else {
        lines.join("\n")
    }
}

fn trim_to(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max_chars.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

