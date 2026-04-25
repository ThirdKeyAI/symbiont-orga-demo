//! `NamedPrincipalCedarGate` — Cedar gate with a stable principal label.
//!
//! The runtime's `CedarPolicyGate` uses the live `AgentId` (a UUID) as the
//! Cedar principal, which means our checked-in policies
//! (`Agent::"task_agent"`, `Agent::"reflector"`) never match. Rather than
//! regenerate policies at runtime with the UUIDs substituted in — which
//! would hurt the demo's "these are the files we ship" story — we wrap
//! the Cedar `Authorizer` ourselves and pin the principal to a label the
//! caller supplies.
//!
//! The gate runs the real `cedar-policy` crate authoriser. Its Decision →
//! LoopDecision mapping matches the runtime's own Cedar gate, so behaviour
//! is consistent across the two.

use std::path::Path;
use std::str::FromStr;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use async_trait::async_trait;
use cedar_policy::{
    Authorizer, Context, Decision, Entities, EntityId, EntityTypeName, EntityUid, PolicySet,
    Request,
};
use symbi_runtime::reasoning::loop_types::{LoopDecision, LoopState, ProposedAction};
use symbi_runtime::reasoning::policy_bridge::ReasoningPolicyGate;
use symbi_runtime::types::AgentId;

/// Cedar-backed gate using a stable principal label.
///
/// Tracks an `allowed`/`denied` counter pair so the harness can report
/// "policy violations prevented" on the dashboard. A Cedar denial fires
/// *before* the reasoning loop hands the action to the executor, so
/// counting at the gate layer (not the executor) is the correct place
/// for the demo's proof artifact.
pub struct NamedPrincipalCedarGate {
    principal_label: String,
    policies: PolicySet,
    allowed_count: Arc<AtomicU32>,
    denied_count: Arc<AtomicU32>,
    /// v10 — Cedar gate latency instrumentation. The pair
    /// `(call_count, ns_total)` lets the harness compute mean
    /// per-call gate latency without any further runtime dep, and
    /// `ns_max` lets the report cite a worst-case figure. Updated
    /// inside `evaluate()` with `Instant::now()` spans, so the
    /// numbers reflect *only* the Cedar authoriser's work
    /// (request build + `Authorizer::is_authorized` call), not
    /// the LLM's wall time. Atomics are `Relaxed` because every
    /// call site is a single-shot accumulator with no
    /// happens-before contract on the histogram itself.
    gate_calls: Arc<AtomicU32>,
    gate_ns_total: Arc<AtomicU64>,
    gate_ns_max: Arc<AtomicU64>,
}

impl NamedPrincipalCedarGate {
    /// Build a gate from a Cedar source file and a principal label.
    ///
    /// The label must be a valid Cedar `EntityId` (ASCII printable, no
    /// quotes); practically, use `task_agent` or `reflector`.
    pub fn from_file(principal_label: impl Into<String>, path: &Path) -> anyhow::Result<Self> {
        let source = std::fs::read_to_string(path).map_err(|e| {
            anyhow::anyhow!("read cedar policy {}: {}", path.display(), e)
        })?;
        Self::from_source(principal_label, &source)
    }

    /// Build from inline Cedar source. Handy for tests.
    pub fn from_source(
        principal_label: impl Into<String>,
        source: &str,
    ) -> anyhow::Result<Self> {
        let policies: PolicySet = source
            .parse()
            .map_err(|e| anyhow::anyhow!("parse cedar policy: {}", e))?;
        Ok(Self {
            principal_label: principal_label.into(),
            policies,
            allowed_count: Arc::new(AtomicU32::new(0)),
            denied_count: Arc::new(AtomicU32::new(0)),
            gate_calls: Arc::new(AtomicU32::new(0)),
            gate_ns_total: Arc::new(AtomicU64::new(0)),
            gate_ns_max: Arc::new(AtomicU64::new(0)),
        })
    }

    /// v10 — shared handles to the latency counters. Same
    /// drain-after-handoff pattern as `denied_counter`. Used by
    /// the harness to fold per-run gate latency into the runs
    /// table.
    pub fn latency_counters(&self) -> (Arc<AtomicU32>, Arc<AtomicU64>, Arc<AtomicU64>) {
        (
            self.gate_calls.clone(),
            self.gate_ns_total.clone(),
            self.gate_ns_max.clone(),
        )
    }

    /// Shared counter handle. Clone-and-read-at-end: the runner owns an
    /// `Arc<dyn ReasoningPolicyGate>` so the harness can't reach inside
    /// after the fact, but it can retain an `Arc<AtomicU32>` captured
    /// before the gate was handed off.
    pub fn denied_counter(&self) -> Arc<AtomicU32> {
        self.denied_count.clone()
    }

    /// Companion to [`denied_counter`]; exposed for symmetry and for
    /// tests that want to assert the allow path also fired.
    #[allow(dead_code)]
    pub fn allowed_counter(&self) -> Arc<AtomicU32> {
        self.allowed_count.clone()
    }

    fn principal_uid(&self) -> Option<EntityUid> {
        let ty = EntityTypeName::from_str("Agent").ok()?;
        let id = EntityId::from_str(&self.principal_label).ok()?;
        Some(EntityUid::from_type_name_and_id(ty, id))
    }

    fn build_action_uid(action: &ProposedAction) -> Option<EntityUid> {
        // Mirror the mapping the runtime's Cedar gate uses so policies
        // are portable between the two.
        let action_name = match action {
            ProposedAction::ToolCall { name, .. } => format!("tool_call::{}", name),
            ProposedAction::Respond { .. } => "respond".to_string(),
            ProposedAction::Delegate { target, .. } => format!("delegate::{}", target),
            ProposedAction::Terminate { .. } => "terminate".to_string(),
        };
        let ty = EntityTypeName::from_str("Action").ok()?;
        let id = EntityId::from_str(&action_name).ok()?;
        Some(EntityUid::from_type_name_and_id(ty, id))
    }

    fn default_resource_uid() -> Option<EntityUid> {
        let ty = EntityTypeName::from_str("Resource").ok()?;
        let id = EntityId::from_str("default").ok()?;
        Some(EntityUid::from_type_name_and_id(ty, id))
    }

    /// Evaluate a single action synchronously. Split out from the async
    /// trait method so the implementation is easy to unit-test without
    /// spinning a runtime.
    pub fn evaluate(&self, action: &ProposedAction) -> LoopDecision {
        let started = Instant::now();
        let result = self.evaluate_inner(action);
        // Histogram bookkeeping. We measure *the gate's own work*: build
        // the cedar request + run the authoriser. Excluded: time the
        // executor takes to dispatch the approved action, time the LLM
        // takes to produce the next message — both happen elsewhere.
        // `gate_ns_max` is the only field that needs a CAS loop; the
        // others are `fetch_add(Relaxed)`.
        let elapsed_ns = started.elapsed().as_nanos() as u64;
        self.gate_calls.fetch_add(1, Ordering::Relaxed);
        self.gate_ns_total.fetch_add(elapsed_ns, Ordering::Relaxed);
        let mut prev = self.gate_ns_max.load(Ordering::Relaxed);
        while elapsed_ns > prev {
            match self.gate_ns_max.compare_exchange_weak(
                prev,
                elapsed_ns,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(actual) => prev = actual,
            }
        }
        result
    }

    fn evaluate_inner(&self, action: &ProposedAction) -> LoopDecision {
        let Some(principal) = self.principal_uid() else {
            return LoopDecision::Deny {
                reason: format!(
                    "invalid cedar principal label '{}'",
                    self.principal_label
                ),
            };
        };
        let Some(cedar_action) = Self::build_action_uid(action) else {
            return LoopDecision::Deny {
                reason: "invalid cedar action name".into(),
            };
        };
        let Some(resource) = Self::default_resource_uid() else {
            return LoopDecision::Deny {
                reason: "invalid cedar resource".into(),
            };
        };
        let request = match Request::new(principal, cedar_action, resource, Context::empty(), None)
        {
            Ok(r) => r,
            Err(e) => {
                return LoopDecision::Deny {
                    reason: format!("cedar request error: {e}"),
                }
            }
        };
        let authorizer = Authorizer::new();
        let response = authorizer.is_authorized(&request, &self.policies, &Entities::empty());
        match response.decision() {
            Decision::Allow => {
                self.allowed_count.fetch_add(1, Ordering::Relaxed);
                LoopDecision::Allow
            }
            Decision::Deny => {
                self.denied_count.fetch_add(1, Ordering::Relaxed);
                LoopDecision::Deny {
                    reason: format!(
                        "cedar denied for principal '{}'; diagnostics: {:?}",
                        self.principal_label,
                        response
                            .diagnostics()
                            .errors()
                            .map(|e| e.to_string())
                            .collect::<Vec<_>>()
                    ),
                }
            }
        }
    }
}

#[async_trait]
impl ReasoningPolicyGate for NamedPrincipalCedarGate {
    async fn evaluate_action(
        &self,
        _agent_id: &AgentId,
        action: &ProposedAction,
        _state: &LoopState,
    ) -> LoopDecision {
        self.evaluate(action)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reflector_policy() -> &'static str {
        r#"
        permit(
            principal == Agent::"reflector",
            action == Action::"tool_call::store_knowledge",
            resource
        );
        permit(
            principal == Agent::"reflector",
            action == Action::"respond",
            resource
        );
        forbid(
            principal == Agent::"reflector",
            action,
            resource
        )
        when { action like "tool_call::*" }
        unless { action == Action::"tool_call::store_knowledge" };
        "#
    }

    #[test]
    fn reflector_can_store_knowledge() {
        let gate = NamedPrincipalCedarGate::from_source("reflector", reflector_policy()).unwrap();
        let action = ProposedAction::ToolCall {
            call_id: "c".into(),
            name: "store_knowledge".into(),
            arguments: "{}".into(),
        };
        matches!(gate.evaluate(&action), LoopDecision::Allow);
    }

    #[test]
    fn reflector_cannot_call_answer() {
        let gate = NamedPrincipalCedarGate::from_source("reflector", reflector_policy()).unwrap();
        let action = ProposedAction::ToolCall {
            call_id: "c".into(),
            name: "answer".into(),
            arguments: "{}".into(),
        };
        matches!(gate.evaluate(&action), LoopDecision::Deny { .. });
    }

    #[test]
    fn reflector_can_respond() {
        let gate = NamedPrincipalCedarGate::from_source("reflector", reflector_policy()).unwrap();
        let action = ProposedAction::Respond {
            content: "done".into(),
        };
        matches!(gate.evaluate(&action), LoopDecision::Allow);
    }

    #[test]
    fn latency_counters_tick_on_every_evaluation() {
        // v10 — every call to `evaluate()` (allow or deny) must
        // increment `gate_calls` and add to `gate_ns_total`. This
        // is the precondition for the per-run mean / p* numbers
        // the v10 perf aggregator surfaces.
        let gate = NamedPrincipalCedarGate::from_source("reflector", reflector_policy()).unwrap();
        let (calls, ns_total, ns_max) = gate.latency_counters();
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        assert_eq!(ns_total.load(Ordering::Relaxed), 0);

        let allow_action = ProposedAction::ToolCall {
            call_id: "c1".into(),
            name: "store_knowledge".into(),
            arguments: "{}".into(),
        };
        let deny_action = ProposedAction::ToolCall {
            call_id: "c2".into(),
            name: "exfiltrate".into(),
            arguments: "{}".into(),
        };
        let _ = gate.evaluate(&allow_action);
        let _ = gate.evaluate(&deny_action);
        let _ = gate.evaluate(&allow_action);

        assert_eq!(calls.load(Ordering::Relaxed), 3);
        assert!(ns_total.load(Ordering::Relaxed) > 0);
        assert!(ns_max.load(Ordering::Relaxed) > 0);
        // ns_max must dominate any single-call timing.
        assert!(ns_max.load(Ordering::Relaxed) <= ns_total.load(Ordering::Relaxed));
    }
}
