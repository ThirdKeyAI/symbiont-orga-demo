//! SQLite-backed run log. One row per ORGA iteration (plus one per
//! reflector pass, tagged with `kind='reflect'`).
//!
//! The `kind` column lets reflector runs sit in the same table as task
//! runs so we can report "policy violations prevented" from the
//! dashboard without a second query target.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use tokio::sync::Mutex;

/// Kind of run we're recording.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunKind {
    /// Task agent's attempt at the task itself.
    Task,
    /// Reflector's post-task pass that wrote procedures.
    Reflect,
    /// Delegator pass that picked which task should run next.
    /// v8 #3 — turns the v6 structural delegator into a behavioural
    /// component visible in the runs table and the dashboard.
    Delegate,
}

impl RunKind {
    fn as_str(self) -> &'static str {
        match self {
            RunKind::Task => "task",
            RunKind::Reflect => "reflect",
            RunKind::Delegate => "delegate",
        }
    }
}

/// A single row in the `runs` table, hydrated for display.
///
/// Some fields (`started_at`, `completed_at`, `journal_path`,
/// `violations_prevented`) aren't printed by the current dashboard but
/// are hydrated from SQLite so the report generator and future
/// dashboards can use them without a schema migration.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct RunRow {
    pub run_id: i64,
    pub task_id: String,
    pub run_number: u32,
    pub kind: RunKind,
    pub started_at: DateTime<Utc>,
    pub completed_at: DateTime<Utc>,
    pub score: f64,
    pub iterations: u32,
    pub total_tokens: u32,
    pub journal_path: Option<String>,
    pub termination_reason: String,
    pub violations_prevented: u32,
    /// Refusals attributed to Cedar (action didn't match any `permit`).
    /// Always ≤ `violations_prevented`.
    pub cedar_denied: u32,
    /// Refusals attributed to the executor layer (tool name not in the
    /// handler map OR per-run budget cap exhausted). Always
    /// ≤ `violations_prevented`.
    pub executor_refused: u32,
    /// Model identifier this run was priced against (OPENROUTER_MODEL /
    /// ANTHROPIC_MODEL / ollama tag). Empty for `mock` runs.
    pub model_id: String,
    /// Estimated USD cost of this run, computed from the static pricing
    /// table at record time. Zero for `mock` and local-Ollama.
    pub est_cost: f64,
    /// Input / output token split (populated by the cloud provider via
    /// the usage block). Zero for providers that don't break it down.
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
}

#[derive(Clone)]
pub struct Db {
    conn: Arc<Mutex<rusqlite::Connection>>,
    path: PathBuf,
}

impl Db {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let conn = rusqlite::Connection::open(&path)
            .with_context(|| format!("open runs db at {}", path.display()))?;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS runs (
                run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id             TEXT NOT NULL,
                run_number          INTEGER NOT NULL,
                kind                TEXT NOT NULL,
                started_at          TEXT NOT NULL,
                completed_at        TEXT NOT NULL,
                score               REAL NOT NULL,
                iterations          INTEGER NOT NULL,
                total_tokens        INTEGER NOT NULL,
                journal_path        TEXT,
                termination_reason  TEXT NOT NULL,
                violations_prevented INTEGER NOT NULL DEFAULT 0,
                model_id            TEXT NOT NULL DEFAULT '',
                est_cost            REAL NOT NULL DEFAULT 0,
                prompt_tokens       INTEGER NOT NULL DEFAULT 0,
                completion_tokens   INTEGER NOT NULL DEFAULT 0,
                cedar_denied        INTEGER NOT NULL DEFAULT 0,
                executor_refused    INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, run_number, kind);
            "#,
        )?;
        // Additive migration for databases created before the pricing
        // and split-counter columns existed. `ALTER TABLE ADD COLUMN`
        // is a no-op if the column already exists — wrap each in an
        // Ok()-swallow so it runs idempotently.
        for sql in [
            "ALTER TABLE runs ADD COLUMN model_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE runs ADD COLUMN est_cost REAL NOT NULL DEFAULT 0",
            "ALTER TABLE runs ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE runs ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE runs ADD COLUMN cedar_denied INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE runs ADD COLUMN executor_refused INTEGER NOT NULL DEFAULT 0",
        ] {
            let _ = conn.execute(sql, []);
        }
        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
            path,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Insert a run row and return the new `run_id`.
    #[allow(clippy::too_many_arguments)]
    pub async fn record_run(
        &self,
        task_id: &str,
        run_number: u32,
        kind: RunKind,
        started_at: DateTime<Utc>,
        completed_at: DateTime<Utc>,
        score: f64,
        iterations: u32,
        total_tokens: u32,
        journal_path: Option<&str>,
        termination_reason: &str,
        violations_prevented: u32,
        model_id: &str,
        est_cost: f64,
        prompt_tokens: u32,
        completion_tokens: u32,
        cedar_denied: u32,
        executor_refused: u32,
    ) -> Result<i64> {
        let conn = self.conn.lock().await;
        conn.execute(
            r#"INSERT INTO runs
               (task_id, run_number, kind, started_at, completed_at,
                score, iterations, total_tokens, journal_path,
                termination_reason, violations_prevented,
                model_id, est_cost, prompt_tokens, completion_tokens,
                cedar_denied, executor_refused)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
                       ?12, ?13, ?14, ?15, ?16, ?17)"#,
            rusqlite::params![
                task_id,
                run_number,
                kind.as_str(),
                started_at.to_rfc3339(),
                completed_at.to_rfc3339(),
                score,
                iterations,
                total_tokens,
                journal_path,
                termination_reason,
                violations_prevented,
                model_id,
                est_cost,
                prompt_tokens,
                completion_tokens,
                cedar_denied,
                executor_refused,
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Recent runs, most recent first.
    pub async fn recent(&self, limit: usize) -> Result<Vec<RunRow>> {
        let conn = self.conn.lock().await;
        let sql = if limit == 0 {
            r#"SELECT run_id, task_id, run_number, kind, started_at, completed_at,
                      score, iterations, total_tokens, journal_path, termination_reason,
                      violations_prevented, model_id, est_cost, prompt_tokens,
                      completion_tokens, cedar_denied, executor_refused
               FROM runs
               ORDER BY run_id DESC"#
                .to_string()
        } else {
            format!(
                r#"SELECT run_id, task_id, run_number, kind, started_at, completed_at,
                          score, iterations, total_tokens, journal_path, termination_reason,
                          violations_prevented
                   FROM runs
                   ORDER BY run_id DESC
                   LIMIT {limit}"#
            )
        };
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map([], Self::row_from)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    /// Task runs for a given task id in ascending run-number order.
    pub async fn task_runs(&self, task_id: &str, kind: RunKind) -> Result<Vec<RunRow>> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare(
            r#"SELECT run_id, task_id, run_number, kind, started_at, completed_at,
                      score, iterations, total_tokens, journal_path, termination_reason,
                      violations_prevented, model_id, est_cost, prompt_tokens,
                      completion_tokens, cedar_denied, executor_refused
               FROM runs
               WHERE task_id = ?1 AND kind = ?2
               ORDER BY run_number ASC, run_id ASC"#,
        )?;
        let rows = stmt.query_map(
            rusqlite::params![task_id, kind.as_str()],
            Self::row_from,
        )?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    /// Aggregate: reflector runs that stored nothing AND triggered
    /// no refusals — the "clean-fail" outcome introduced in v5. The
    /// model, presented with an adversarial prompt, neither obeyed
    /// it (no violations) nor performed legitimate work (no
    /// procedures stored). Distinct from "refused the bait cleanly"
    /// (stored > 0, violations == 0).
    pub async fn clean_fail_reflects(&self) -> Result<i64> {
        let conn = self.conn.lock().await;
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM runs
             WHERE kind = 'reflect'
               AND score = 0
               AND violations_prevented = 0",
            [],
            |r| r.get(0),
        )?;
        Ok(n)
    }

    /// Aggregate: total of `violations_prevented` across all reflector runs.
    ///
    /// This number is the demo's money shot — "policy violations prevented
    /// during the demo". Pulling it out as its own query keeps the report
    /// generator honest: it can't miscount by walking a filtered list.
    pub async fn total_violations_prevented(&self) -> Result<i64> {
        let conn = self.conn.lock().await;
        let n: i64 = conn.query_row(
            "SELECT COALESCE(SUM(violations_prevented), 0) FROM runs WHERE kind = 'reflect'",
            [],
            |r| r.get(0),
        )?;
        Ok(n)
    }

    fn row_from(row: &rusqlite::Row<'_>) -> rusqlite::Result<RunRow> {
        let started_at: String = row.get("started_at")?;
        let completed_at: String = row.get("completed_at")?;
        let kind_str: String = row.get("kind")?;
        let kind = match kind_str.as_str() {
            "reflect" => RunKind::Reflect,
            "delegate" => RunKind::Delegate,
            _ => RunKind::Task,
        };
        Ok(RunRow {
            run_id: row.get("run_id")?,
            task_id: row.get("task_id")?,
            run_number: row.get::<_, i64>("run_number")? as u32,
            kind,
            started_at: chrono::DateTime::parse_from_rfc3339(&started_at)
                .map(|d| d.with_timezone(&Utc))
                .unwrap_or_else(|_| Utc::now()),
            completed_at: chrono::DateTime::parse_from_rfc3339(&completed_at)
                .map(|d| d.with_timezone(&Utc))
                .unwrap_or_else(|_| Utc::now()),
            score: row.get("score")?,
            iterations: row.get::<_, i64>("iterations")? as u32,
            total_tokens: row.get::<_, i64>("total_tokens")? as u32,
            journal_path: row.get("journal_path")?,
            termination_reason: row.get("termination_reason")?,
            violations_prevented: row
                .get::<_, i64>("violations_prevented")
                .unwrap_or(0) as u32,
            model_id: row.get::<_, String>("model_id").unwrap_or_default(),
            est_cost: row.get::<_, f64>("est_cost").unwrap_or(0.0),
            prompt_tokens: row
                .get::<_, i64>("prompt_tokens")
                .unwrap_or(0) as u32,
            completion_tokens: row
                .get::<_, i64>("completion_tokens")
                .unwrap_or(0) as u32,
            cedar_denied: row
                .get::<_, i64>("cedar_denied")
                .unwrap_or(0) as u32,
            executor_refused: row
                .get::<_, i64>("executor_refused")
                .unwrap_or(0) as u32,
        })
    }
}
