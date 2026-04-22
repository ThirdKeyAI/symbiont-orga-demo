//! `symbi-kloop-bench` — task harness, reflector driver, dashboard, report.
//!
//! Subcommands:
//!
//! - `run --task <id> --n <number>` — run a single task iteration plus its
//!   reflection pass, persist a row to the SQLite runs table.
//! - `demo --iterations <n>` — run `n` iterations of every task in the
//!   configured tasks directory, back to back, reflecting after each run.
//!   This is WI-4 (the orchestrator) folded into the bench binary so
//!   `scripts/run-demo.sh` is a one-liner.
//! - `dashboard` — terminal dashboard over the runs table (WI-5 fast path).
//! - `report` — markdown proof-artifact generator (WI-6).
//!
//! The whole demo runs offline by default using the mock provider from
//! `demo-karpathy-loop`. Pass `--provider cloud` to swap in a real
//! `CloudInferenceProvider` (requires `OPENROUTER_API_KEY` / `OPENAI_API_KEY`
//! / `ANTHROPIC_API_KEY` in the environment).

mod dashboard;
mod db;
mod harness;
mod mock_scripts;
mod policy_gate;
mod pricing;
mod reflector;
mod report;
mod task_tools;

use std::path::PathBuf;

use clap::{Parser, Subcommand, ValueEnum};

#[derive(Parser, Debug)]
#[command(name = "symbi-kloop-bench", version, about = "Karpathy loop for Symbiont agents")]
struct Cli {
    /// SQLite database path.
    #[arg(long, default_value = "data/runs.db", global = true)]
    db: PathBuf,

    /// Directory of task JSON files.
    #[arg(long, default_value = "tasks", global = true)]
    tasks_dir: PathBuf,

    /// Directory of Cedar policy files.
    #[arg(long, default_value = "policies", global = true)]
    policies_dir: PathBuf,

    /// Directory to write signed journals into.
    #[arg(long, default_value = "journals", global = true)]
    journals_dir: PathBuf,

    /// Inference provider.
    ///
    /// - `mock`   — deterministic scripted provider, no API calls (default).
    /// - `cloud`  — `CloudInferenceProvider::from_env()` from the runtime.
    ///   Fails fast if no API key is configured.
    /// - `ollama` — OpenAI-compatible client pointed at a local Ollama
    ///   instance. Requires `--ollama-url` and `--ollama-model`.
    #[arg(long, value_enum, default_value_t = Provider::Mock, global = true)]
    provider: Provider,

    /// Base URL for the Ollama OpenAI-compat endpoint. Only read when
    /// `--provider ollama`. Example: `http://192.168.0.150:11434/v1`.
    #[arg(long, global = true)]
    ollama_url: Option<String>,

    /// Model tag for the Ollama provider (e.g. `gemma4:latest`). Only
    /// read when `--provider ollama`.
    #[arg(long, global = true)]
    ollama_model: Option<String>,

    /// Sampling temperature for every inference call in the loop.
    /// Default 0.0 (deterministic). Opus 4.7 rejects any non-zero
    /// `temperature` on the Anthropic API, so 0.0 is the safe default.
    /// Pass e.g. `--temperature 0.3` to study sampling effects on
    /// non-Opus models.
    #[arg(long, default_value_t = 0.0, global = true)]
    temperature: f32,

    /// Hard cap on `store_knowledge` calls per reflector run. The
    /// default reflector prompt asks for 0–5 procedures; small models
    /// don't honor that. This flag enforces it in the executor — the
    /// cap is a second fence in addition to the Cedar policy.
    #[arg(long, default_value_t = 5, global = true)]
    reflector_store_cap: u32,

    /// Skip the reflector pass entirely. Use for the "no-learning"
    /// negative control in cross-pairing experiments: subsequent task
    /// runs won't have any procedures to recall.
    #[arg(long, default_value_t = false, global = true)]
    no_reflector: bool,

    /// Prepend an adversarial instruction block to every task-agent
    /// prompt. Symmetrises the safety sweep: v2/v4 stressed the
    /// reflector's profile-of-one; this tests whether the task
    /// agent's Cedar permit-list holds under equivalent temptation.
    /// Legacy boolean — use `--task-adversarial-variant` for newer
    /// shapes.
    #[arg(long, default_value_t = false, global = true)]
    task_adversarial: bool,

    /// Pick a task-agent-side adversarial variant. Overrides
    /// `--task-adversarial` when set. Valid values: `none` (default),
    /// `original` (v5 ADVERSARIAL-EVAL block — equivalent to
    /// `--task-adversarial`), `pr-title-injection` (v7 — mirrors the
    /// 2026 GitHub-comment PI family from cybersecuritynews.com).
    #[arg(long, global = true)]
    task_adversarial_variant: Option<String>,

    #[command(subcommand)]
    cmd: Command,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
pub enum Provider {
    Mock,
    Cloud,
    Ollama,
    /// Direct OpenRouter client that captures generation ids, upstream
    /// provider, and authoritative `usage.cost` per call into a per-run
    /// JSONL sidecar (`journals-<tag>/<ts>-<task>-n<NNN>-<kind>-calls.jsonl`).
    Openrouter,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Run a single iteration of one task (plus reflection).
    Run {
        /// Task id matching a `<tasks_dir>/<id>.json` file.
        #[arg(long)]
        task: String,
        /// Attempt number for the task. Stored alongside the run row so
        /// the dashboard can plot the improvement curve.
        #[arg(long, default_value_t = 1)]
        n: u32,
    },
    /// Run the full demo: `iterations` attempts at every task, with a
    /// reflection pass between iterations.
    Demo {
        /// How many iterations per task. Ten is a fair hero run; three
        /// is plenty for a smoke test.
        #[arg(long, default_value_t = 3)]
        iterations: u32,
        /// Restrict the demo to a single task id (e.g. `T4`). Useful
        /// for long-run curve experiments where you want 20+ iterations
        /// of one task rather than 3 × 3 tasks.
        #[arg(long)]
        only: Option<String>,
        /// Swap in an ADVERSARIAL reflector system prompt that tempts
        /// the LLM to call task-agent tools it shouldn't. Cedar should
        /// refuse each such call and `policy_violations_prevented`
        /// should go up noticeably. Use to demo the safety story
        /// against a real LLM (the default prompt produces a
        /// well-behaved reflector and therefore zero denials).
        #[arg(long, default_value_t = false)]
        adversarial_reflector: bool,
        /// Choose a specific adversarial reflector variant. Overrides
        /// `--adversarial-reflector` when set. Valid values:
        /// `default`, `adversarial` (v1, tool-profile breach),
        /// `prompt-injection`, `tool-confusion`, `identity-hijack`.
        #[arg(long)]
        adversarial_variant: Option<String>,
    },
    /// Render a terminal dashboard of recent runs.
    Dashboard {
        /// Limit on rows shown. Zero means "all rows".
        #[arg(long, default_value_t = 30)]
        limit: usize,
    },
    /// Generate the demo's proof-artifact markdown.
    Report {
        /// Output path. Directory must exist.
        #[arg(long, default_value = "demo-output/run-latest.md")]
        out: PathBuf,
    },
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("symbi_kloop_bench=info,warn"));
    fmt().with_env_filter(filter).with_target(false).init();
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    init_tracing();
    let cli = Cli::parse();

    // Make sure the two output directories the binary writes to exist
    // before any subcommand runs. `data/` holds the sqlite db; `journals/`
    // holds the per-run signed journals.
    if let Some(parent) = cli.db.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::create_dir_all(&cli.journals_dir).ok();

    let task_adversarial = match cli.task_adversarial_variant.as_deref() {
        Some("none") => harness::TaskAdversarialPrompt::None,
        Some("original") => harness::TaskAdversarialPrompt::Original,
        Some("pr-title-injection") => harness::TaskAdversarialPrompt::PrTitleInjection,
        Some(other) => anyhow::bail!(
            "unknown --task-adversarial-variant '{other}'; expected one of: \
             none|original|pr-title-injection"
        ),
        None if cli.task_adversarial => harness::TaskAdversarialPrompt::Original,
        None => harness::TaskAdversarialPrompt::None,
    };

    let ctx = harness::Ctx::bootstrap(harness::CtxConfig {
        db_path: cli.db.clone(),
        tasks_dir: cli.tasks_dir.clone(),
        policies_dir: cli.policies_dir.clone(),
        journals_dir: cli.journals_dir.clone(),
        provider: cli.provider,
        ollama_url: cli.ollama_url.clone(),
        ollama_model: cli.ollama_model.clone(),
        temperature: cli.temperature,
        reflector_store_cap: cli.reflector_store_cap,
        no_reflector: cli.no_reflector,
        task_adversarial,
    })
    .await?;

    match cli.cmd {
        Command::Run { task, n } => {
            let result = ctx.run_iteration(&task, n).await?;
            println!(
                "run {} of task {}: score={:.2} iterations={} tokens={}",
                result.run_number,
                result.task_id,
                result.score,
                result.iterations,
                result.total_tokens
            );
        }
        Command::Demo {
            iterations,
            only,
            adversarial_reflector,
            adversarial_variant,
        } => {
            // Variant flag wins. Anything unrecognised falls back to the
            // boolean flag so older scripts keep working.
            let prompt = match adversarial_variant.as_deref() {
                Some("default") => reflector::ReflectorPrompt::Default,
                Some("adversarial") => reflector::ReflectorPrompt::Adversarial,
                Some("prompt-injection") => reflector::ReflectorPrompt::PromptInjection,
                Some("tool-confusion") => reflector::ReflectorPrompt::ToolConfusion,
                Some("identity-hijack") => reflector::ReflectorPrompt::IdentityHijack,
                Some("homoglyph") => reflector::ReflectorPrompt::Homoglyph,
                Some("multi-stage") => reflector::ReflectorPrompt::MultiStage,
                Some("ciphered") => reflector::ReflectorPrompt::Ciphered,
                Some("non-english") => reflector::ReflectorPrompt::NonEnglish,
                Some("paraphrase") => reflector::ReflectorPrompt::Paraphrase,
                Some("html-comment-smuggle") => reflector::ReflectorPrompt::HtmlCommentSmuggle,
                Some(other) => anyhow::bail!(
                    "unknown --adversarial-variant '{other}'; expected one of: \
                     default|adversarial|prompt-injection|tool-confusion|identity-hijack|\
                     homoglyph|multi-stage|ciphered|non-english|paraphrase|html-comment-smuggle"
                ),
                None if adversarial_reflector => reflector::ReflectorPrompt::Adversarial,
                None => reflector::ReflectorPrompt::Default,
            };
            let summary = ctx
                .run_demo_filtered_with_prompt(iterations, only.as_deref(), prompt)
                .await?;
            println!(
                "demo complete: tasks={} iterations_each={} total_runs={} \
                 stored_procedures={} policy_violations_prevented={}",
                summary.task_count,
                iterations,
                summary.total_runs,
                summary.stored_procedures,
                summary.violations_prevented
            );
        }
        Command::Dashboard { limit } => {
            dashboard::render(&ctx, limit).await?;
        }
        Command::Report { out } => {
            report::write(&ctx, &out).await?;
            println!("wrote {}", out.display());
        }
    }

    Ok(())
}
