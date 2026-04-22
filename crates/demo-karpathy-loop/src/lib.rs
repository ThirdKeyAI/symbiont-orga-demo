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

pub mod delegator_executor;
pub mod executor;
pub mod knowledge;
pub mod ollama_provider;
pub mod openrouter_provider;
pub mod provider;
pub mod reflector_executor;
pub mod task;

pub use delegator_executor::DelegatorActionExecutor;
pub use executor::TaskActionExecutor;
pub use knowledge::{sanitize_field, KnowledgeStore, Procedure};
pub use ollama_provider::OllamaInferenceProvider;
pub use openrouter_provider::{CallLog, OpenRouterInferenceProvider};
pub use provider::MockInferenceProvider;
pub use reflector_executor::{RawArgsRecord, ReflectorActionExecutor};
pub use task::{ScoreDetail, Task, TaskOutcome};
