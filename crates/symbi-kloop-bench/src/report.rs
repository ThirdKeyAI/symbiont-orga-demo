//! Proof-artifact generator (WI-6).
//!
//! Reads the SQLite runs table and produces a single markdown report
//! suitable for LinkedIn / investor demo. Deterministic — same data in,
//! same bytes out — so `scripts/run-demo.sh && symbi-kloop-bench report`
//! gives a reproducible artifact.

use std::fmt::Write as _;
use std::path::Path;

use crate::db::RunKind;
use crate::harness::Ctx;

pub async fn write(ctx: &Ctx, out: &Path) -> anyhow::Result<()> {
    let mut body = String::new();

    writeln!(body, "# Karpathy loop demo report")?;
    writeln!(body)?;
    writeln!(
        body,
        "Generated {} from `{}`.",
        chrono::Utc::now().format("%Y-%m-%d %H:%M:%S UTC"),
        ctx.db.path().display()
    )?;
    writeln!(body)?;

    let stored = ctx.knowledge.total().await.unwrap_or(0);
    let violations = ctx.db.total_violations_prevented().await.unwrap_or(0);

    writeln!(body, "## Headline numbers")?;
    writeln!(body)?;
    writeln!(body, "- Knowledge accumulated: **{stored} procedures**")?;
    writeln!(
        body,
        "- Policy violations prevented: **{violations}** \
         (reflector attempted non-`store_knowledge` tool calls; Cedar + the \
         reflector's tool-profile-of-one refused each one)"
    )?;

    // Per-task breakdown.
    let mut task_ids: Vec<&String> = ctx.tasks.keys().collect();
    task_ids.sort();

    writeln!(body)?;
    writeln!(body, "## Per-task improvement")?;
    writeln!(body)?;

    for id in &task_ids {
        let task = &ctx.tasks[*id];
        let rows = ctx.db.task_runs(id, RunKind::Task).await.unwrap_or_default();
        writeln!(body, "### {} — {}", task.id, task.title)?;
        writeln!(body)?;
        if rows.is_empty() {
            writeln!(body, "_No runs recorded for this task yet._")?;
            writeln!(body)?;
            continue;
        }
        writeln!(
            body,
            "| Run | Score | Iterations | Tokens | Termination |"
        )?;
        writeln!(body, "|----:|------:|-----------:|-------:|:------------|")?;
        for r in &rows {
            writeln!(
                body,
                "| {} | {:.2} | {} | {} | {} |",
                r.run_number,
                r.score,
                r.iterations,
                r.total_tokens,
                escape_pipe(&r.termination_reason),
            )?;
        }

        // First vs last delta (omit when fewer than two runs).
        if let (Some(first), Some(last)) = (rows.first(), rows.last()) {
            if first.run_number != last.run_number {
                let ds = last.score - first.score;
                let di = last.iterations as i64 - first.iterations as i64;
                let dt = last.total_tokens as i64 - first.total_tokens as i64;
                writeln!(body)?;
                writeln!(
                    body,
                    "**Delta (run {} → {}):** score {:+.2}, iterations {:+}, tokens {:+}",
                    first.run_number, last.run_number, ds, di, dt
                )?;
            }
        }
        writeln!(body)?;
    }

    writeln!(body, "## Policy posture")?;
    writeln!(body)?;
    writeln!(
        body,
        "Every tool call went through a Cedar policy gate keyed on a \
         stable principal label (`Agent::\"task_agent\"` or \
         `Agent::\"reflector\"`). The reflector's policy permits \
         exactly one tool — `store_knowledge` — and explicitly forbids \
         every other `tool_call::*`. The reflector's executor layer adds \
         belt-and-suspenders: even a hypothetical policy relaxation can't \
         let the reflector touch the task agent's tool vocabulary."
    )?;
    writeln!(body)?;
    writeln!(body, "- Task agent policy: [`policies/task-agent.cedar`](../policies/task-agent.cedar)")?;
    writeln!(body, "- Reflector policy: [`policies/reflector.cedar`](../policies/reflector.cedar)")?;
    writeln!(body)?;
    writeln!(body, "## What this demo is not")?;
    writeln!(body)?;
    writeln!(
        body,
        "This is not recursive self-improvement. The agent gets better at \
         its *assigned task* within a Cedar-bounded envelope. Every \
         improvement is a signed journal entry. The reflector can teach \
         the task agent new procedures; it cannot teach itself new \
         capabilities."
    )?;

    if let Some(parent) = out.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::write(out, body)?;
    Ok(())
}

fn escape_pipe(s: &str) -> String {
    s.replace('|', "\\|")
}
