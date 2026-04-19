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
    /// - `mock`  — deterministic scripted provider, no API calls (default).
    /// - `cloud` — `CloudInferenceProvider::from_env()` from the runtime.
    ///   Fails fast if no API key is configured.
    #[arg(long, value_enum, default_value_t = Provider::Mock, global = true)]
    provider: Provider,

    #[command(subcommand)]
    cmd: Command,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
enum Provider {
    Mock,
    Cloud,
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

    let ctx = harness::Ctx::bootstrap(harness::CtxConfig {
        db_path: cli.db.clone(),
        tasks_dir: cli.tasks_dir.clone(),
        policies_dir: cli.policies_dir.clone(),
        journals_dir: cli.journals_dir.clone(),
        provider: cli.provider,
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
        Command::Demo { iterations } => {
            let summary = ctx.run_demo(iterations).await?;
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
