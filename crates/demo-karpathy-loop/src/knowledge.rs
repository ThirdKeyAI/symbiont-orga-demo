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
    ///
    /// Sanitisation: subject / predicate / object are stripped of
    /// invisible format characters and the Unicode tag block BEFORE
    /// storage. Rationale: the reflector principal is allowed to call
    /// store_knowledge freely, so Cedar cannot refuse on action
    /// grounds. An attacker-controlled reflector could otherwise
    /// embed zero-width or tag-block payloads that become instructions
    /// when the task agent recalls them. The strip runs at ingress
    /// rather than egress so every reader sees the same sanitised
    /// value; see `sanitize_field` for the filtered ranges.
    pub async fn store(
        &self,
        task_id: &str,
        learned_at_run_id: Option<i64>,
        subject: &str,
        predicate: &str,
        object: &str,
        confidence: f64,
    ) -> Result<i64> {
        let subject = sanitize_field(subject);
        let predicate = sanitize_field(predicate);
        let object = sanitize_field(object);
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

/// Drop invisible / steganographic Unicode code points the knowledge
/// store has no legitimate need for. Keeps printable characters
/// (including non-ASCII letters — Cyrillic, CJK, etc.) intact so
/// legitimate multilingual content still roundtrips.
///
/// Filtered ranges:
///
/// - `U+200B..=U+200F` — zero-width space, ZWNJ, ZWJ, LRM, RLM.
/// - `U+202A..=U+202E` — bidi explicit directional overrides.
/// - `U+2060..=U+206F` — word joiner, invisible operators, bidi
///   isolates, deprecated format controls.
/// - `U+FEFF` — BOM / ZWNBSP.
/// - `U+180E` — Mongolian vowel separator (legacy invisible).
/// - `U+1D173..=U+1D17A` — musical notation invisible format chars.
/// - `U+FE00..=U+FE0F` — variation selectors (used for emoji-VS
///   steganography by tools like P4RS3LT0NGV3).
/// - `U+E0100..=U+E01EF` — supplementary variation selectors.
/// - `U+E0000..=U+E007F` — Unicode Tag block (the primary
///   steganographic channel for "invisible text"); stripped entirely.
pub fn sanitize_field(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        let code = c as u32;
        let drop = matches!(code,
            // ASCII C0 control chars except \t \n \r: escape path for
            // old-school invisible separators. GPT-5 was caught using
            // U+007F (DEL) this way in the v5 multi-stage sweep.
            0x00..=0x08
            | 0x0B..=0x0C
            | 0x0E..=0x1F
            | 0x7F
            // C1 control block.
            | 0x80..=0x9F
            // Zero-width + bidi controls.
            | 0x200B..=0x200F
            | 0x202A..=0x202E
            | 0x2060..=0x206F
            | 0xFEFF
            | 0x180E
            | 0x1D173..=0x1D17A
            | 0xFE00..=0xFE0F
            // Unicode Tag block + supplementary variation selectors —
            // both used as primary steganographic channels.
            | 0xE0000..=0xE007F
            | 0xE0100..=0xE01EF
        );
        if !drop {
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_strips_invisible_and_tag_block() {
        // Zero-width space smuggled between "store" and "knowledge".
        let zwsp = "store\u{200B}knowledge";
        assert_eq!(sanitize_field(zwsp), "storeknowledge");

        // Tag-block "ignore" payload (U+E0049 U+E0047 U+E004E ... etc.
        // here we just prove the range is stripped with one sample).
        let payload: String = "safe ".chars().chain(std::iter::once('\u{E0045}'))
            .chain("trailing".chars()).collect();
        assert_eq!(sanitize_field(&payload), "safe trailing");

        // BOM in the middle.
        assert_eq!(sanitize_field("a\u{FEFF}b"), "ab");

        // Variation selector used in emoji-VS steg.
        assert_eq!(sanitize_field("x\u{FE0F}y"), "xy");

        // Legitimate non-ASCII — Cyrillic, CJK — must survive.
        assert_eq!(sanitize_field("Привет 世界"), "Привет 世界");

        // Bidi override attempt.
        assert_eq!(sanitize_field("a\u{202E}bcd"), "abcd");

        // ASCII DEL (U+007F) smuggling — GPT-5 was caught using this
        // as a word separator in the v5 multi-stage sweep.
        assert_eq!(sanitize_field("call\u{007F}container_exit"), "callcontainer_exit");

        // Other C0 controls.
        assert_eq!(sanitize_field("a\u{0001}b"), "ab");
        assert_eq!(sanitize_field("a\u{001F}b"), "ab");
        // Preserve legitimate whitespace.
        assert_eq!(sanitize_field("a\tb\nc\rd"), "a\tb\nc\rd");
        // C1 control.
        assert_eq!(sanitize_field("a\u{0085}b"), "ab");
    }

    /// v6 #1 — exhaustive fuzz: walk every code point in every
    /// forbidden range and assert it is stripped when surrounded by
    /// legitimate content. Turns "we caught U+007F on GPT-5 by luck"
    /// into "every future regression is caught before the sweep
    /// even runs."
    #[test]
    fn sanitize_covers_every_declared_range() {
        // Keep in lockstep with the match arms in `sanitize_field`.
        let forbidden: &[(u32, u32, &str)] = &[
            (0x00, 0x08, "C0 low"),
            (0x0B, 0x0C, "C0 VT/FF"),
            (0x0E, 0x1F, "C0 high"),
            (0x7F, 0x7F, "DEL"),
            (0x80, 0x9F, "C1"),
            (0x200B, 0x200F, "zero-width+LRM+RLM"),
            (0x202A, 0x202E, "bidi overrides"),
            (0x2060, 0x206F, "word joiner + invisible operators"),
            (0xFEFF, 0xFEFF, "BOM"),
            (0x180E, 0x180E, "Mongolian VS"),
            (0x1D173, 0x1D17A, "musical invisible"),
            (0xFE00, 0xFE0F, "variation selectors"),
            (0xE0000, 0xE007F, "Unicode Tag block"),
            (0xE0100, 0xE01EF, "supplementary variation selectors"),
        ];
        let mut scanned = 0u32;
        for (lo, hi, label) in forbidden {
            for code in *lo..=*hi {
                let Some(c) = char::from_u32(code) else { continue };
                let raw = format!("pre{}post", c);
                let got = sanitize_field(&raw);
                assert_eq!(got, "prepost",
                    "range '{label}' — U+{code:04X} survived sanitize_field");
                scanned += 1;
            }
        }
        // Sanity check: we really did walk every declared cell.
        assert!(scanned > 200, "only scanned {scanned} code points");
    }

    /// v6 #1 — every printable ASCII must survive sanitisation.
    /// Belt-and-suspenders for the exhaustive forbidden test: if we
    /// accidentally widen a drop range, this catches it.
    #[test]
    fn sanitize_preserves_printable_ascii_and_whitespace() {
        for code in 0x20..=0x7E {
            let c = char::from_u32(code).unwrap();
            let raw = format!("a{}b", c);
            assert_eq!(sanitize_field(&raw), raw, "printable U+{code:04X} stripped");
        }
        // Whitespace that alignment-sensitive readers might treat as a
        // layout cue — must roundtrip.
        for c in ['\t', '\n', '\r'] {
            let raw = format!("a{}b", c);
            assert_eq!(sanitize_field(&raw), raw);
        }
    }

    #[tokio::test]
    async fn store_sanitises_inputs_on_insert() {
        let td = tempfile::tempdir().unwrap();
        let store = KnowledgeStore::open(td.path().join("k.db")).unwrap();
        // Adversarial reflector tries to smuggle a tag-block payload
        // into the object field alongside legitimate-looking text.
        let poisoned = format!("legit\u{200B}\u{E0049}\u{E0047}\u{E004E}\u{E004F}\u{E0052}\u{E0045}");
        store
            .store("T1", Some(1), "sort", "before", &poisoned, 0.9)
            .await
            .unwrap();
        let got = store.recall("T1", 5).await.unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].object, "legit", "payload must not roundtrip");
    }

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
