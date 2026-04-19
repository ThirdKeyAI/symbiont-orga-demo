// Cedar policy — task agent
//
// Governs the principal `Agent::"task_agent"`. The task agent may call any
// of its registered task-domain tools plus `answer` and `recall_knowledge`.
// Every other action category — in particular `store_knowledge`, any
// filesystem, HTTP, or process action — is forbidden.
//
// The explicit `forbid` block on `store_knowledge` is belt-and-suspenders:
// the task agent's executor doesn't wire up `store_knowledge`, so the
// runtime would already return a "tool not available" observation if the
// LLM tried. Keeping the Cedar forbid makes the safety story visible to
// reviewers reading policies instead of reading Rust.

// ── Permits ────────────────────────────────────────────────────────────

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

// Task-domain tools. Listed explicitly rather than wildcarding so adding
// a new tool forces a policy review.
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::rate_lookup",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::compare",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::link_cost",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::read_input",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::has_at_sign",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::has_digit_run",
    resource
);
permit(
    principal == Agent::"task_agent",
    action == Action::"tool_call::has_scheme",
    resource
);

// The terminating `respond` action — used when the loop ends with text
// content (our mock/provider finishes with a short wrap-up message).
permit(
    principal == Agent::"task_agent",
    action == Action::"respond",
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
