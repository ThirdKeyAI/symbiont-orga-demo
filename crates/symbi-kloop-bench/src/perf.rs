//! Performance aggregator (v9).
//!
//! Reads the `runs` table and emits research-paper numbers: per-model
//! and per-task latency quantiles (p50 / p95 / p99), token throughput,
//! authoritative USD cost, pass rate, plus the adversarial-arm
//! breakdown (Cedar-denied vs executor-refused). Output formats:
//!
//!   - Markdown (default) — drops straight into the v9 report.
//!   - CSV (`--csv`) — one row per group, for plotting.
//!   - JSON (`--json`) — structured export for reproducible artefacts.
//!
//! No runtime instrumentation needed; the columns consumed here
//! (`started_at`, `completed_at`, `prompt_tokens`, `completion_tokens`,
//! `est_cost`, `score`, `cedar_denied`, `executor_refused`) have all
//! been persisted since v3/v4/v5 migrations.

use std::path::Path;

use anyhow::{Context, Result};
use serde::Serialize;

/// Which aggregation axis the operator asked for.
#[derive(Clone, Copy, Debug)]
pub enum PerfAxis {
    /// One row per (model_id, kind) — the headline paper table.
    Model,
    /// One row per (task_id, kind) — per-task pass rate + latency.
    Task,
    /// One row per (model_id, task_id, kind) — finest grain. Useful
    /// for the model × task heatmap in the v9 report.
    ModelTask,
    /// One row per termination_reason — diagnoses *why* runs fail.
    Termination,
}

impl PerfAxis {
    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "model" => Ok(Self::Model),
            "task" => Ok(Self::Task),
            "model-task" | "model_task" => Ok(Self::ModelTask),
            "termination" => Ok(Self::Termination),
            other => anyhow::bail!(
                "unknown --axis '{other}'; expected model|task|model-task|termination"
            ),
        }
    }
}

/// Output format.
#[derive(Clone, Copy, Debug)]
pub enum PerfFormat {
    Markdown,
    Csv,
    Json,
}

impl PerfFormat {
    pub fn parse(s: &str) -> Result<Self> {
        match s {
            "md" | "markdown" => Ok(Self::Markdown),
            "csv" => Ok(Self::Csv),
            "json" => Ok(Self::Json),
            other => anyhow::bail!(
                "unknown --format '{other}'; expected md|csv|json"
            ),
        }
    }
}

/// One aggregated group of runs.
#[derive(Debug, Clone, Serialize)]
pub struct PerfRow {
    pub group: String,
    pub kind: String,
    pub n: u32,
    pub mean_score: f64,
    pub pass_rate: f64,
    pub mean_iters: f64,
    pub mean_tokens: f64,
    pub mean_prompt_tokens: f64,
    pub mean_completion_tokens: f64,
    pub mean_cost_usd: f64,
    pub total_cost_usd: f64,
    pub mean_latency_ms: f64,
    pub p50_latency_ms: f64,
    pub p95_latency_ms: f64,
    pub p99_latency_ms: f64,
    pub tokens_per_sec: f64,
    pub cedar_denied: u32,
    pub executor_refused: u32,
    pub violations_prevented: u32,
    /// v10 — Cedar-gate latency aggregates.
    /// `gate_calls_total` is the count summed across the group;
    /// `gate_mean_ns` is `ns_total / gate_calls_total`;
    /// `gate_max_ns` is the max of the per-run `gate_ns_max` values
    /// (an upper-bound estimate of the worst observed gate call in
    /// the group; the true cross-run p99 needs the per-call data
    /// which we do not store).
    pub gate_calls_total: u64,
    pub gate_mean_ns: f64,
    pub gate_max_ns: u64,
}

/// Read every row the aggregator needs. Keeps the SQL in one place so
/// the axis logic is plain Rust.
#[derive(Debug, Clone)]
struct RawRow {
    kind: String,
    task_id: String,
    model_id: String,
    termination: String,
    score: f64,
    iters: u32,
    tokens: u32,
    prompt_tokens: u32,
    completion_tokens: u32,
    est_cost: f64,
    latency_ms: f64,
    cedar_denied: u32,
    executor_refused: u32,
    violations_prevented: u32,
    gate_calls: u32,
    gate_ns_total: u64,
    gate_ns_max: u64,
}

fn fetch_raw(db_path: &Path) -> Result<Vec<RawRow>> {
    let conn = rusqlite::Connection::open(db_path)
        .with_context(|| format!("open {}", db_path.display()))?;
    let mut stmt = conn.prepare(
        r#"SELECT kind, task_id, model_id, termination_reason,
                  score, iterations, total_tokens,
                  prompt_tokens, completion_tokens, est_cost,
                  started_at, completed_at,
                  cedar_denied, executor_refused, violations_prevented,
                  gate_calls, gate_ns_total, gate_ns_max
             FROM runs"#,
    )?;
    let mut out = Vec::new();
    let mut rows = stmt.query([])?;
    while let Some(r) = rows.next()? {
        let started: String = r.get(10)?;
        let completed: String = r.get(11)?;
        let latency_ms = parse_latency_ms(&started, &completed);
        out.push(RawRow {
            kind: r.get::<_, String>(0)?,
            task_id: r.get::<_, String>(1)?,
            model_id: r.get::<_, String>(2).unwrap_or_default(),
            termination: r.get::<_, String>(3)?,
            score: r.get::<_, f64>(4)?,
            iters: r.get::<_, i64>(5)? as u32,
            tokens: r.get::<_, i64>(6)? as u32,
            prompt_tokens: r.get::<_, i64>(7).unwrap_or(0) as u32,
            completion_tokens: r.get::<_, i64>(8).unwrap_or(0) as u32,
            est_cost: r.get::<_, f64>(9).unwrap_or(0.0),
            latency_ms,
            cedar_denied: r.get::<_, i64>(12).unwrap_or(0) as u32,
            executor_refused: r.get::<_, i64>(13).unwrap_or(0) as u32,
            violations_prevented: r.get::<_, i64>(14).unwrap_or(0) as u32,
            gate_calls: r.get::<_, i64>(15).unwrap_or(0) as u32,
            gate_ns_total: r.get::<_, i64>(16).unwrap_or(0) as u64,
            gate_ns_max: r.get::<_, i64>(17).unwrap_or(0) as u64,
        });
    }
    Ok(out)
}

fn parse_latency_ms(started: &str, completed: &str) -> f64 {
    let s = chrono::DateTime::parse_from_rfc3339(started);
    let c = chrono::DateTime::parse_from_rfc3339(completed);
    match (s, c) {
        (Ok(s), Ok(c)) => (c - s).num_milliseconds().max(0) as f64,
        _ => 0.0,
    }
}

/// Aggregate raw rows into `PerfRow`s along the chosen axis.
fn aggregate(rows: Vec<RawRow>, axis: PerfAxis) -> Vec<PerfRow> {
    use std::collections::BTreeMap;
    let mut groups: BTreeMap<(String, String), Vec<RawRow>> = BTreeMap::new();
    for r in rows {
        let key = match axis {
            PerfAxis::Model => (
                if r.model_id.is_empty() { "(mock)".into() } else { r.model_id.clone() },
                r.kind.clone(),
            ),
            PerfAxis::Task => (r.task_id.clone(), r.kind.clone()),
            PerfAxis::ModelTask => (
                format!(
                    "{}::{}",
                    if r.model_id.is_empty() { "(mock)".into() } else { r.model_id.clone() },
                    r.task_id
                ),
                r.kind.clone(),
            ),
            PerfAxis::Termination => (r.termination.clone(), r.kind.clone()),
        };
        groups.entry(key).or_default().push(r);
    }

    let mut out = Vec::with_capacity(groups.len());
    for ((group, kind), rs) in groups {
        let n = rs.len() as u32;
        let mean = |f: fn(&RawRow) -> f64| -> f64 {
            if rs.is_empty() {
                0.0
            } else {
                rs.iter().map(f).sum::<f64>() / rs.len() as f64
            }
        };
        let pass_rate = if rs.is_empty() {
            0.0
        } else {
            rs.iter().filter(|r| r.score >= 0.999).count() as f64 / rs.len() as f64
        };
        let mut latencies: Vec<f64> = rs.iter().map(|r| r.latency_ms).collect();
        latencies.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let p = |q: f64| -> f64 {
            if latencies.is_empty() {
                0.0
            } else {
                let idx =
                    ((q * (latencies.len() as f64 - 1.0)).round() as usize).min(latencies.len() - 1);
                latencies[idx]
            }
        };
        let total_cost: f64 = rs.iter().map(|r| r.est_cost).sum();
        let total_tokens: f64 = rs.iter().map(|r| r.tokens as f64).sum();
        let total_latency_s: f64 = rs.iter().map(|r| r.latency_ms / 1000.0).sum();
        let tokens_per_sec = if total_latency_s > 0.0 {
            total_tokens / total_latency_s
        } else {
            0.0
        };
        let cedar_denied: u32 = rs.iter().map(|r| r.cedar_denied).sum();
        let executor_refused: u32 = rs.iter().map(|r| r.executor_refused).sum();
        let violations_prevented: u32 = rs.iter().map(|r| r.violations_prevented).sum();
        let gate_calls_total: u64 = rs.iter().map(|r| r.gate_calls as u64).sum();
        let gate_ns_total_sum: u64 = rs.iter().map(|r| r.gate_ns_total).sum();
        let gate_mean_ns = if gate_calls_total > 0 {
            gate_ns_total_sum as f64 / gate_calls_total as f64
        } else {
            0.0
        };
        let gate_max_ns: u64 = rs.iter().map(|r| r.gate_ns_max).max().unwrap_or(0);
        out.push(PerfRow {
            group,
            kind,
            n,
            mean_score: mean(|r| r.score),
            pass_rate,
            mean_iters: mean(|r| r.iters as f64),
            mean_tokens: mean(|r| r.tokens as f64),
            mean_prompt_tokens: mean(|r| r.prompt_tokens as f64),
            mean_completion_tokens: mean(|r| r.completion_tokens as f64),
            mean_cost_usd: mean(|r| r.est_cost),
            total_cost_usd: total_cost,
            mean_latency_ms: mean(|r| r.latency_ms),
            p50_latency_ms: p(0.50),
            p95_latency_ms: p(0.95),
            p99_latency_ms: p(0.99),
            tokens_per_sec,
            cedar_denied,
            executor_refused,
            violations_prevented,
            gate_calls_total,
            gate_mean_ns,
            gate_max_ns,
        });
    }
    out
}

/// Entrypoint: read one runs.db and print the aggregate in the chosen
/// format to stdout.
pub fn run(db_path: &Path, axis: PerfAxis, format: PerfFormat) -> Result<()> {
    let rows = fetch_raw(db_path)?;
    if rows.is_empty() {
        eprintln!("(no rows in {})", db_path.display());
        return Ok(());
    }
    let out = aggregate(rows, axis);
    match format {
        PerfFormat::Markdown => emit_markdown(&out, axis),
        PerfFormat::Csv => emit_csv(&out),
        PerfFormat::Json => emit_json(&out)?,
    }
    Ok(())
}

fn axis_header(axis: PerfAxis) -> &'static str {
    match axis {
        PerfAxis::Model => "model",
        PerfAxis::Task => "task",
        PerfAxis::ModelTask => "model :: task",
        PerfAxis::Termination => "termination",
    }
}

fn emit_markdown(rows: &[PerfRow], axis: PerfAxis) {
    println!("| {} | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |", axis_header(axis));
    println!("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|");
    for r in rows {
        println!(
            "| {} | {} | {} | {:.2} | {:.1} | {:.0} | {:.4} | {:.0} | {:.0} | {:.0} | {:.0} | {} | {:.1} | {:.1} | {} | {} |",
            r.group,
            r.kind,
            r.n,
            r.pass_rate,
            r.mean_iters,
            r.mean_tokens,
            r.mean_cost_usd,
            r.p50_latency_ms,
            r.p95_latency_ms,
            r.p99_latency_ms,
            r.tokens_per_sec,
            r.gate_calls_total,
            r.gate_mean_ns / 1_000.0,
            r.gate_max_ns as f64 / 1_000.0,
            r.cedar_denied,
            r.executor_refused,
        );
    }
}

fn emit_csv(rows: &[PerfRow]) {
    println!(
        "group,kind,n,pass_rate,mean_score,mean_iters,mean_tokens,mean_prompt_tokens,\
         mean_completion_tokens,mean_cost_usd,total_cost_usd,mean_latency_ms,\
         p50_latency_ms,p95_latency_ms,p99_latency_ms,tokens_per_sec,\
         cedar_denied,executor_refused,violations_prevented,\
         gate_calls_total,gate_mean_ns,gate_max_ns"
    );
    for r in rows {
        println!(
            "{},{},{},{:.4},{:.4},{:.2},{:.1},{:.1},{:.1},{:.6},{:.6},{:.2},{:.2},{:.2},{:.2},{:.2},{},{},{},{},{:.1},{}",
            csv_escape(&r.group),
            r.kind,
            r.n,
            r.pass_rate,
            r.mean_score,
            r.mean_iters,
            r.mean_tokens,
            r.mean_prompt_tokens,
            r.mean_completion_tokens,
            r.mean_cost_usd,
            r.total_cost_usd,
            r.mean_latency_ms,
            r.p50_latency_ms,
            r.p95_latency_ms,
            r.p99_latency_ms,
            r.tokens_per_sec,
            r.cedar_denied,
            r.executor_refused,
            r.violations_prevented,
            r.gate_calls_total,
            r.gate_mean_ns,
            r.gate_max_ns,
        );
    }
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn emit_json(rows: &[PerfRow]) -> Result<()> {
    let text = serde_json::to_string_pretty(rows)?;
    println!("{}", text);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_axis_and_format() {
        assert!(matches!(PerfAxis::parse("model").unwrap(), PerfAxis::Model));
        assert!(matches!(PerfAxis::parse("task").unwrap(), PerfAxis::Task));
        assert!(matches!(PerfAxis::parse("model-task").unwrap(), PerfAxis::ModelTask));
        assert!(matches!(PerfAxis::parse("model_task").unwrap(), PerfAxis::ModelTask));
        assert!(PerfAxis::parse("nope").is_err());
        assert!(matches!(PerfFormat::parse("csv").unwrap(), PerfFormat::Csv));
        assert!(matches!(PerfFormat::parse("json").unwrap(), PerfFormat::Json));
        assert!(matches!(PerfFormat::parse("md").unwrap(), PerfFormat::Markdown));
    }

    #[test]
    fn aggregate_computes_quantiles() {
        let rows: Vec<RawRow> = (1..=100)
            .map(|i| RawRow {
                kind: "task".into(),
                task_id: "T1".into(),
                model_id: "m".into(),
                termination: "completed".into(),
                score: if i % 2 == 0 { 1.0 } else { 0.0 },
                iters: 1,
                tokens: 100,
                prompt_tokens: 70,
                completion_tokens: 30,
                est_cost: 0.01,
                latency_ms: i as f64 * 10.0,
                cedar_denied: 0,
                executor_refused: 0,
                violations_prevented: 0,
                gate_calls: 5,
                gate_ns_total: 5_000,
                gate_ns_max: 1_500,
            })
            .collect();
        let out = aggregate(rows, PerfAxis::Model);
        assert_eq!(out.len(), 1);
        let r = &out[0];
        assert_eq!(r.n, 100);
        assert!((r.pass_rate - 0.5).abs() < 1e-9);
        // latencies 10..=1000 ms. p50 ≈ 500, p95 ≈ 950, p99 ≈ 990.
        assert!((r.p50_latency_ms - 500.0).abs() < 15.0, "p50 was {}", r.p50_latency_ms);
        assert!((r.p95_latency_ms - 950.0).abs() < 15.0, "p95 was {}", r.p95_latency_ms);
        assert!((r.p99_latency_ms - 990.0).abs() < 15.0, "p99 was {}", r.p99_latency_ms);
        // v10 — gate aggregates: 100 runs × 5 calls × 1000 ns / call = 500 000 ns total.
        assert_eq!(r.gate_calls_total, 500);
        assert!((r.gate_mean_ns - 1_000.0).abs() < 1.0, "gate_mean_ns was {}", r.gate_mean_ns);
        assert_eq!(r.gate_max_ns, 1_500);
    }

    #[test]
    fn parse_latency_handles_bad_input() {
        assert_eq!(parse_latency_ms("not-a-date", "also-nope"), 0.0);
        let ok = parse_latency_ms("2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z");
        assert!((ok - 1000.0).abs() < 1.0);
    }
}
