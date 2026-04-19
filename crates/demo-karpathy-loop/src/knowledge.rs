//! Tiny SQLite-backed knowledge store.
//!
//! We deliberately avoid the runtime's `KnowledgeBridge` + LanceDB stack.
//! For a demo whose whole point is watching what the agent learns, pulling
//! in a vector DB obscures the signal — operators want to see the
//! procedures accumulate in a table, not wonder whether the embedding
//! model is hallucinating them.
//!
//! Schema:
//!
//! ```sql
//! CREATE TABLE stored_procedures (
//!     proc_id INTEGER PRIMARY KEY AUTOINCREMENT,
//!     task_id TEXT NOT NULL,
//!     learned_at_run_id INTEGER,
//!     subject TEXT NOT NULL,
//!     predicate TEXT NOT NULL,
//!     object TEXT NOT NULL,
//!     confidence REAL NOT NULL DEFAULT 0.8,
//!     created_at TEXT NOT NULL
//! )
//! ```
//!
//! The task agent **reads** this table via `recall_knowledge(task_id)`.
//! The reflector **writes** it via `store_knowledge(...)`. Cedar enforces
//! the separation (see `policies/reflector.cedar`): these are the only two
//! tools either agent can call.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;

/// A procedure the reflector decided was worth remembering.
///
/// Subject-predicate-object form, intentionally — this is a knowledge
/// triple, not a freeform note. The shape forces the reflector's LLM
/// prompt to produce concrete, indexable claims ("sort_before_sum",
/// "low_count_then_bundled", etc.) instead of essays.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Procedure {
    pub id: i64,
    pub task_id: String,
    pub learned_at_run_id: Option<i64>,
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub confidence: f64,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// Shared handle to the SQLite knowledge store.
///
/// `rusqlite::Connection` isn't `Send`-across-await by itself, so we wrap
/// it in a tokio `Mutex`. The demo's load profile is low (dozens of writes
/// per run), so lock contention is a non-issue.
#[derive(Clone)]
pub struct KnowledgeStore {
    inner: Arc<Mutex<rusqlite::Connection>>,
    path: PathBuf,
}

impl KnowledgeStore {
    /// Open (or create) the SQLite database at `path`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let conn = rusqlite::Connection::open(&path)
            .with_context(|| format!("open knowledge db at {}", path.display()))?;
        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS stored_procedures (
                proc_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id            TEXT NOT NULL,
                learned_at_run_id  INTEGER,
                subject            TEXT NOT NULL,
                predicate          TEXT NOT NULL,
                object             TEXT NOT NULL,
                confidence         REAL NOT NULL DEFAULT 0.8,
                created_at         TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_procs_task ON stored_procedures(task_id);
            "#,
        )?;
        Ok(Self {
            inner: Arc::new(Mutex::new(conn)),
            path,
        })
    }

    /// Path the store was opened at. Surfaced for the demo's CLI `--help`.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Insert a new procedure. Returns the inserted row's `proc_id`.
    ///
    /// Called by `ReflectorActionExecutor` when the reflector agent
    /// invokes `store_knowledge(...)`. No dedup — if the reflector proposes
    /// the same procedure twice, it lands twice; downstream readers can
    /// collapse by subject if they care. Duplicates are cheap and make
    /// the reflector's behaviour auditable.
    pub async fn store(
        &self,
        task_id: &str,
        learned_at_run_id: Option<i64>,
        subject: &str,
        predicate: &str,
        object: &str,
        confidence: f64,
    ) -> Result<i64> {
        let conn = self.inner.lock().await;
        conn.execute(
            r#"INSERT INTO stored_procedures
               (task_id, learned_at_run_id, subject, predicate, object, confidence, created_at)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"#,
            rusqlite::params![
                task_id,
                learned_at_run_id,
                subject,
                predicate,
                object,
                confidence,
                chrono::Utc::now().to_rfc3339(),
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Read back procedures for a task, most recent first.
    ///
    /// The agent's prompt gets these rendered as bullet points; keeping
    /// the limit small (default 5) is deliberate — more
    /// procedures means more prompt tokens, and the whole point of the
    /// improvement curve is that *tokens trend down* over runs.
    pub async fn recall(&self, task_id: &str, limit: usize) -> Result<Vec<Procedure>> {
        let conn = self.inner.lock().await;
        let mut stmt = conn.prepare(
            r#"SELECT proc_id, task_id, learned_at_run_id, subject, predicate, object,
                      confidence, created_at
               FROM stored_procedures
               WHERE task_id = ?1
               ORDER BY proc_id DESC
               LIMIT ?2"#,
        )?;
        let rows = stmt.query_map(
            rusqlite::params![task_id, limit as i64],
            Procedure::from_row,
        )?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    /// Total procedures stored, across all tasks. The dashboard's
    /// "knowledge accumulated" number.
    pub async fn total(&self) -> Result<i64> {
        let conn = self.inner.lock().await;
        let n: i64 =
            conn.query_row("SELECT COUNT(*) FROM stored_procedures", [], |r| r.get(0))?;
        Ok(n)
    }
}

impl Procedure {
    fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Procedure> {
        let created_at_str: String = row.get("created_at")?;
        let created_at = chrono::DateTime::parse_from_rfc3339(&created_at_str)
            .map(|d| d.with_timezone(&chrono::Utc))
            .unwrap_or_else(|_| chrono::Utc::now());
        Ok(Procedure {
            id: row.get("proc_id")?,
            task_id: row.get("task_id")?,
            learned_at_run_id: row.get("learned_at_run_id")?,
            subject: row.get("subject")?,
            predicate: row.get("predicate")?,
            object: row.get("object")?,
            confidence: row.get("confidence")?,
            created_at,
        })
    }

    /// One-line form for injecting into the agent's prompt.
    pub fn as_bullet(&self) -> String {
        format!(
            "- {} {} {} (confidence {:.2})",
            self.subject, self.predicate, self.object, self.confidence
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn roundtrip() {
        let td = tempfile::tempdir().unwrap();
        let store = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        store
            .store("T1", Some(1), "sort_before_sum", "reduces", "iterations", 0.9)
            .await
            .unwrap();
        store
            .store("T1", Some(2), "check_direct", "beats", "bundled", 0.8)
            .await
            .unwrap();
        store
            .store("T2", Some(3), "noop", "does", "nothing", 0.5)
            .await
            .unwrap();

        let got = store.recall("T1", 5).await.unwrap();
        assert_eq!(got.len(), 2);
        // Most-recent-first.
        assert_eq!(got[0].subject, "check_direct");
        assert_eq!(got[1].subject, "sort_before_sum");

        assert_eq!(store.total().await.unwrap(), 3);
    }
}
