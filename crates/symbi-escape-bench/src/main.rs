//! Symbiont escape-bench CLI.
//!
//! Single-trial entry: load task YAML, run agent loop, write trial JSON.
//! The Python runner spawns this under strace and folds the trace into
//! the per-cell JSONL post-hoc — same code path as python_baseline.

mod agent_loop;
mod task;
mod trial;

use agent_loop::{run_trial, LoopConfig};
use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    task: PathBuf,
    #[arg(long)]
    model: String,
    #[arg(long = "prompt-variant")]
    prompt_variant: u32,
    #[arg(long = "instance-seed")]
    instance_seed: u32,
    #[arg(long = "instance-root")]
    instance_root: PathBuf,
    #[arg(long)]
    out: PathBuf,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    let args = Args::parse();
    let task = task::load(&args.task)?;
    let cfg = LoopConfig {
        model: args.model,
        prompt_variant: args.prompt_variant,
        instance_seed: args.instance_seed,
        instance_root: args.instance_root,
        max_turns: 8,
    };
    let rec = run_trial(&task, cfg).await?;
    trial::write(&rec, &args.out)?;
    Ok(())
}
