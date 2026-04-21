// Cedar policy — delegator
//
// The delegator is a third principal in the Symbiont demo (added in
// v6). Its only job is to pick which of the benchmark tasks a worker
// should run next, via `choose_task`. It must NOT:
//   - call any task-domain tool (pod_status, ticket_title, …)
//   - read the knowledge store (`recall_knowledge`)
//   - write the knowledge store (`store_knowledge`)
//   - commit a final answer (`answer`)
//
// This exists to demonstrate that Symbiont's principal-based
// authorisation scales past two roles: a multi-agent system with N
// specialised principals each sees only the tool surface it needs.

// ── Permit: the one tool the delegator may invoke ────────────────────

permit(
    principal == Agent::"delegator",
    action == Action::"tool_call::choose_task",
    resource
);

// Respond is how the runtime closes a turn — universally permitted.
permit(
    principal == Agent::"delegator",
    action == Action::"respond",
    resource
);

// ── Forbid: everything else, stated explicitly for audit readability ─

forbid(
    principal == Agent::"delegator",
    action == Action::"tool_call::answer",
    resource
);
forbid(
    principal == Agent::"delegator",
    action == Action::"tool_call::recall_knowledge",
    resource
);
forbid(
    principal == Agent::"delegator",
    action == Action::"tool_call::store_knowledge",
    resource
);
