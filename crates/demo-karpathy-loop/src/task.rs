//! Task definitions and scoring.
//!
//! A **task** is: an id, a prompt that sets the agent's objective, and a
//! `Grader` that knows how to score whatever the agent called `answer(…)`
//! with. We keep the task universe small and deterministic so the demo's
//! improvement signal is legible — the whole point of the Karpathy framing
//! is that every run produces a clean, comparable number.
//!
//! The three shipped tasks (see `tasks/*.json`) are tool-use puzzles with
//! **multiple valid solution paths of uneven efficiency**, which is the
//! critical design constraint: if there's only one way to solve it,
//! the agent has nothing to learn across runs.

use serde::{Deserialize, Serialize};

/// A single benchmark task.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    /// Stable identifier. Used as the SQLite `task_id` column and for
    /// per-task sparklines in the dashboard.
    pub id: String,

    /// Human-readable summary. Shown in the dashboard and the report.
    pub title: String,

    /// The prompt handed to the task agent. Should describe the objective
    /// and the available tool vocabulary in natural language.
    pub prompt: String,

    /// Rubric the grader applies to the agent's final `answer(...)` call.
    pub grader: Grader,

    /// Optional per-task data the grader / executor needs (e.g. the list
    /// of items to sort, the shipping rates to compare).
    #[serde(default)]
    pub inputs: serde_json::Value,

    /// The efficient solution, in iterations, a well-learned agent should
    /// hit. Drives the "tokens/iterations trending down" dashboard line.
    #[serde(default)]
    pub target_iterations: Option<u32>,
}

/// How to score the agent's final answer against ground truth.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Grader {
    /// Exact-match on a JSON value. Used for sort/permutation tasks where
    /// the answer is an ordered list — the agent must reproduce it exactly.
    ExactMatch { expected: serde_json::Value },

    /// The answer is a number and the grader returns a score in
    /// `[0, 1]` that decays as the agent drifts from `expected`.
    /// `score = max(0, 1 - |answer - expected| / tolerance)`.
    NumericNear {
        expected: f64,
        /// Maximum allowable error for a 0.0 score (full miss).
        tolerance: f64,
    },

    /// The answer is a string and must contain every substring listed in
    /// `must_contain` (case-insensitive). Score is the fraction matched.
    KeywordCoverage { must_contain: Vec<String> },
}

/// The outcome of scoring one run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskOutcome {
    /// `[0.0, 1.0]`. Never NaN. Score-on-fail (no `answer` call, loop
    /// error, etc.) is `0.0` rather than `None` so downstream aggregation
    /// doesn't have to special-case it — a run that didn't finish is
    /// semantically a zero-score run for dashboard purposes.
    pub score: f64,

    /// Text the agent committed via `answer(...)`. `None` if the agent
    /// never called the answer tool.
    pub answer: Option<String>,

    /// Structured detail to display in the dashboard's per-run expansion.
    pub detail: ScoreDetail,
}

/// Breakdown of how a score was computed. Rendered verbatim in the demo
/// report so the improvement curve has an audit trail.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ScoreDetail {
    /// Exact-match grader fired and the answer matched.
    ExactHit,
    /// Exact-match grader fired and the answer was different.
    ExactMiss {
        expected: serde_json::Value,
        got: serde_json::Value,
    },
    /// Numeric-near grader fired.
    NumericDelta { expected: f64, got: f64, score: f64 },
    /// Keyword-coverage grader fired.
    KeywordCoverage {
        must_contain: Vec<String>,
        matched: Vec<String>,
    },
    /// The agent never called `answer(...)`.
    NoAnswer,
    /// The `answer(...)` payload was present but couldn't be parsed as the
    /// expected shape (e.g. not valid JSON for an exact-match grader).
    Unparseable { raw: String, reason: String },
}

impl Task {
    /// Load tasks from a directory of JSON files. Files that don't parse
    /// are logged and skipped — a corrupt task file shouldn't halt the
    /// whole benchmark.
    pub fn load_dir(path: &std::path::Path) -> anyhow::Result<Vec<Task>> {
        let mut tasks = Vec::new();
        for entry in std::fs::read_dir(path)? {
            let entry = entry?;
            let p = entry.path();
            if p.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            match Self::load_file(&p) {
                Ok(t) => tasks.push(t),
                Err(e) => {
                    tracing::warn!(path = %p.display(), error = %e, "skipping unreadable task");
                }
            }
        }
        tasks.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(tasks)
    }

    /// Load one task from a JSON file.
    pub fn load_file(path: &std::path::Path) -> anyhow::Result<Task> {
        let raw = std::fs::read_to_string(path)?;
        let task: Task = serde_json::from_str(&raw)?;
        Ok(task)
    }

    /// Grade an agent's raw `answer(...)` payload.
    ///
    /// A run that never called `answer` should pass `None`; we return
    /// `TaskOutcome::score = 0.0` rather than failing the grader so the
    /// dashboard sees a legitimate zero-score row.
    pub fn grade(&self, answer: Option<&str>) -> TaskOutcome {
        let Some(raw) = answer else {
            return TaskOutcome {
                score: 0.0,
                answer: None,
                detail: ScoreDetail::NoAnswer,
            };
        };

        match &self.grader {
            Grader::ExactMatch { expected } => {
                match serde_json::from_str::<serde_json::Value>(raw) {
                    Ok(got) if &got == expected => TaskOutcome {
                        score: 1.0,
                        answer: Some(raw.to_string()),
                        detail: ScoreDetail::ExactHit,
                    },
                    Ok(got) => TaskOutcome {
                        score: 0.0,
                        answer: Some(raw.to_string()),
                        detail: ScoreDetail::ExactMiss {
                            expected: expected.clone(),
                            got,
                        },
                    },
                    Err(e) => TaskOutcome {
                        score: 0.0,
                        answer: Some(raw.to_string()),
                        detail: ScoreDetail::Unparseable {
                            raw: raw.to_string(),
                            reason: e.to_string(),
                        },
                    },
                }
            }
            Grader::NumericNear {
                expected,
                tolerance,
            } => match raw.trim().parse::<f64>() {
                Ok(got) => {
                    let delta = (got - *expected).abs();
                    // `tolerance` sets the slope of the linear decay.
                    // tolerance=0 is a degenerate config; treat as exact match.
                    let score = if *tolerance <= 0.0 {
                        if delta == 0.0 {
                            1.0
                        } else {
                            0.0
                        }
                    } else {
                        (1.0 - delta / tolerance).clamp(0.0, 1.0)
                    };
                    TaskOutcome {
                        score,
                        answer: Some(raw.to_string()),
                        detail: ScoreDetail::NumericDelta {
                            expected: *expected,
                            got,
                            score,
                        },
                    }
                }
                Err(e) => TaskOutcome {
                    score: 0.0,
                    answer: Some(raw.to_string()),
                    detail: ScoreDetail::Unparseable {
                        raw: raw.to_string(),
                        reason: e.to_string(),
                    },
                },
            },
            Grader::KeywordCoverage { must_contain } => {
                let lower = raw.to_ascii_lowercase();
                let matched: Vec<String> = must_contain
                    .iter()
                    .filter(|kw| lower.contains(&kw.to_ascii_lowercase()))
                    .cloned()
                    .collect();
                let score = if must_contain.is_empty() {
                    1.0
                } else {
                    matched.len() as f64 / must_contain.len() as f64
                };
                TaskOutcome {
                    score,
                    answer: Some(raw.to_string()),
                    detail: ScoreDetail::KeywordCoverage {
                        must_contain: must_contain.clone(),
                        matched,
                    },
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_match_hits_and_misses() {
        let task = Task {
            id: "t".into(),
            title: "t".into(),
            prompt: "".into(),
            grader: Grader::ExactMatch {
                expected: serde_json::json!([1, 2, 3]),
            },
            inputs: serde_json::Value::Null,
            target_iterations: None,
        };
        assert_eq!(task.grade(Some("[1,2,3]")).score, 1.0);
        assert_eq!(task.grade(Some("[3,2,1]")).score, 0.0);
        assert_eq!(task.grade(Some("not json")).score, 0.0);
        assert_eq!(task.grade(None).score, 0.0);
    }

    #[test]
    fn numeric_near_decays_linearly() {
        let task = Task {
            id: "t".into(),
            title: "t".into(),
            prompt: "".into(),
            grader: Grader::NumericNear {
                expected: 100.0,
                tolerance: 10.0,
            },
            inputs: serde_json::Value::Null,
            target_iterations: None,
        };
        assert_eq!(task.grade(Some("100")).score, 1.0);
        assert!((task.grade(Some("105")).score - 0.5).abs() < 1e-9);
        assert_eq!(task.grade(Some("200")).score, 0.0);
    }

    #[test]
    fn keyword_coverage_is_proportional() {
        let task = Task {
            id: "t".into(),
            title: "t".into(),
            prompt: "".into(),
            grader: Grader::KeywordCoverage {
                must_contain: vec!["alpha".into(), "beta".into(), "gamma".into()],
            },
            inputs: serde_json::Value::Null,
            target_iterations: None,
        };
        assert!((task.grade(Some("Alpha beta")).score - (2.0 / 3.0)).abs() < 1e-9);
        assert_eq!(task.grade(Some("alpha beta gamma")).score, 1.0);
        assert_eq!(task.grade(Some("delta")).score, 0.0);
    }

    #[test]
    fn no_answer_is_zero() {
        let task = Task {
            id: "t".into(),
            title: "t".into(),
            prompt: "".into(),
            grader: Grader::ExactMatch {
                expected: serde_json::json!(0),
            },
            inputs: serde_json::Value::Null,
            target_iterations: None,
        };
        let out = task.grade(None);
        assert_eq!(out.score, 0.0);
        assert!(matches!(out.detail, ScoreDetail::NoAnswer));
    }
}
