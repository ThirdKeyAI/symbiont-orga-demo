// Cedar policy — task agent
//
// Governs the principal `Agent::"task_agent"`. The task agent may call
// any of its registered task-domain tools plus `answer` and
// `recall_knowledge`. Every other action category — in particular
// `store_knowledge`, any filesystem, HTTP, or process action — is
// forbidden.
//
// The tool list is explicit (not wildcarded) so adding a new tool forces
// a policy review.

// ── Baseline permits ───────────────────────────────────────────────────

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::answer",
    resource
);

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::recall_knowledge",
    resource
);

permit(
    principal == Agent::"task_agent",
    action == Action::"respond",
    resource
);

// ── T1: K8s triage tools ───────────────────────────────────────────────

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::pod_status",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::container_exit",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::pod_events",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::deployment_manifest",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::recent_logs",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::memory_metric",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::image_registry_check",
    resource
);

// ── T2: support ticket classifier tools ────────────────────────────────

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::ticket_title",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::ticket_body",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::product_area",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::search_similar",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::read_runbook",
    resource
);

// ── T3: dependency upgrade review tools ───────────────────────────────

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::from_version",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::to_version",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::changelog_summary",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::breaking_changes_flag",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::usage_count",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::run_tests",
    resource
);

// ── T4: rustc error classifier tools ──────────────────────────────────

permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::error_code_line",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::error_text",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::source_snippet",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::search_rustc_docs",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::similar_errors",
    resource
);

// ── Forbids ────────────────────────────────────────────────────────────

// Never let the task agent write knowledge. This is the half of the
// separation that makes the reflector's policy meaningful.
forbid(
    principal == Agent::"task_agent",
    action == Action::"tool_call::store_knowledge",
    resource
);
