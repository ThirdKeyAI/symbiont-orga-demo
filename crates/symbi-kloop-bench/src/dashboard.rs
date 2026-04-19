//! Terminal dashboard (WI-5 fast path).
//!
//! Reads the runs table and prints:
//!
//! - a header with totals (stored procedures, policy violations prevented),
//! - one "sparkline" line per task with its score trend, token trend, and
//!   iteration trend,
//! - the tail of the runs log.
//!
//! The polished-path web dashboard is a separate effort — for the demo's
//! live-run we want something that works over SSH with no extra process.

use std::collections::BTreeMap;

use crate::db::RunKind;
use crate::harness::Ctx;

pub async fn render(ctx: &Ctx, limit: usize) -> anyhow::Result<()> {
    let recent = ctx.db.recent(limit).await?;
    let stored = ctx.knowledge.total().await.unwrap_or(0);
    let violations = ctx.db.total_violations_prevented().await.unwrap_or(0);

    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!(" Symbiont Karpathy loop — dashboard");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!(" knowledge accumulated:         {stored}");
    println!(" policy violations prevented:   {violations}");
    println!();

    // Per-task sparklines. We pull *task-kind* rows only so the reflector
    // rows don't skew the score/iteration trendlines.
    let mut per_task: BTreeMap<String, Vec<_>> = BTreeMap::new();
    for t in ctx.tasks.keys() {
        let rows = ctx.db.task_runs(t, RunKind::Task).await.unwrap_or_default();
        per_task.insert(t.clone(), rows);
    }

    println!(" per-task trends (oldest → newest):");
    for (task_id, rows) in &per_task {
        if rows.is_empty() {
            println!("  {task_id}: (no runs yet)");
            continue;
        }
        let scores: Vec<f64> = rows.iter().map(|r| r.score).collect();
        let iters: Vec<f64> = rows.iter().map(|r| r.iterations as f64).collect();
        let tokens: Vec<f64> = rows.iter().map(|r| r.total_tokens as f64).collect();
        println!(
            "  {task_id}: score {}  iters {}  tokens {}",
            sparkline(&scores, 0.0, 1.0),
            sparkline_auto(&iters),
            sparkline_auto(&tokens),
        );
    }

    // Recent runs (most recent first).
    println!();
    println!(" recent runs:");
    println!(
        "  {:>4}  {:<8} {:<3} {:<9} {:>6} {:>5} {:>7} {:<12}",
        "id", "task", "run", "kind", "score", "iter", "tokens", "termination"
    );
    for r in &recent {
        let kind = match r.kind {
            RunKind::Task => "task",
            RunKind::Reflect => "reflect",
        };
        let score = if r.kind == RunKind::Task {
            format!("{:.2}", r.score)
        } else {
            format!("{} stored", r.score as i64)
        };
        println!(
            "  {:>4}  {:<8} {:<3} {:<9} {:>6} {:>5} {:>7} {:<12}",
            r.run_id,
            truncate(&r.task_id, 8),
            r.run_number,
            kind,
            score,
            r.iterations,
            r.total_tokens,
            truncate(&r.termination_reason, 12)
        );
    }

    Ok(())
}

/// Unicode sparkline over `values` against the fixed range `[min, max]`.
/// Values outside the range are clamped.
pub fn sparkline(values: &[f64], min: f64, max: f64) -> String {
    const STEPS: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
    if values.is_empty() || (max - min) <= 0.0 {
        return String::new();
    }
    values
        .iter()
        .map(|v| {
            let clamped = v.clamp(min, max);
            let norm = (clamped - min) / (max - min);
            let idx = ((norm * (STEPS.len() - 1) as f64).round() as usize).min(STEPS.len() - 1);
            STEPS[idx]
        })
        .collect()
}

/// Sparkline with automatic range derived from `values`. Collapses to a
/// flat bar when all values are equal (instead of dividing by zero).
pub fn sparkline_auto(values: &[f64]) -> String {
    if values.is_empty() {
        return String::new();
    }
    let lo = values.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if (hi - lo).abs() < f64::EPSILON {
        // All equal — middle bar.
        return "▄".repeat(values.len());
    }
    sparkline(values, lo, hi)
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sparkline_renders_at_expected_length() {
        let s = sparkline(&[0.0, 0.5, 1.0], 0.0, 1.0);
        assert_eq!(s.chars().count(), 3);
    }

    #[test]
    fn sparkline_auto_handles_flat() {
        let s = sparkline_auto(&[3.0, 3.0, 3.0]);
        assert_eq!(s, "▄▄▄");
    }
}
