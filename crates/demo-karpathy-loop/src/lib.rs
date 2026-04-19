//! # Karpathy-loop demo: shared types
//!
//! This crate is the bottom layer of the demo. It contains:
//!
//! - `task` — task definitions and scoring logic
//! - `executor` — the `TaskActionExecutor` that speaks the task-domain tool
//!   vocabulary (`answer`, `compare`, `sum`, `recall_knowledge`)
//! - `reflector_executor` — the reflector's tool-profile-of-one executor
//!   (`store_knowledge` only)
//! - `knowledge` — a tiny SQLite-backed knowledge store the agent reads from
//!   and the reflector writes to. We deliberately do NOT use the runtime's
//!   `KnowledgeBridge` because that brings in LanceDB / Qdrant. The demo's
//!   value is in what the agent *learns*, not in the embedding pipeline.
//! - `provider` — a deterministic mock `InferenceProvider` so the whole demo
//!   compiles and runs end-to-end without an API key.
//!
//! The binary crate `symbi-kloop-bench` consumes everything from here.

pub mod executor;
pub mod knowledge;
pub mod provider;
pub mod reflector_executor;
pub mod task;

pub use executor::TaskActionExecutor;
pub use knowledge::{KnowledgeStore, Procedure};
pub use provider::MockInferenceProvider;
pub use reflector_executor::ReflectorActionExecutor;
pub use task::{ScoreDetail, Task, TaskOutcome};
