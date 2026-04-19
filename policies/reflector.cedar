// Cedar policy — reflector
//
// This is the policy that makes the Karpathy-loop demo safe to show. The
// reflector agent exists solely to propose new procedures for the task
// agent to use on the next run. Everything it does must flow through
// `store_knowledge`; every other action — including calling the task
// agent's own tools, reading the filesystem, or contacting an external
// service — is forbidden.
//
// The `unless` form on the global forbid is deliberate. It reads as
// "forbid tool_call unless the tool is store_knowledge", which is the
// exact sentence we want in the demo script. The explicit permit below
// is still present because Cedar's authorizer requires at least one
// permit to grant access, and we'd rather that single permit be the
// only way to grant it.

// Reflector can store knowledge. That's it.
permit(
    principal == Agent::"reflector",
    action == Action::"tool_call::store_knowledge",
    resource
);

// Allow the reflector to emit its terminating text response (the LLM's
// "I've recorded these procedures" wrap-up). Without this, the loop
// cannot terminate cleanly.
permit(
    principal == Agent::"reflector",
    action == Action::"respond",
    resource
);

// Global forbid on *any* tool_call that isn't store_knowledge. If a
// future change to the tool profile accidentally widens the reflector's
// surface, this clause still holds.
forbid(
    principal == Agent::"reflector",
    action,
    resource
)
when { action like "tool_call::*" }
unless { action == Action::"tool_call::store_knowledge" };

// The reflector cannot modify its own policy, its own prompt, or the
// task agent's config. These resource types don't exist in the default
// demo wiring — this clause is a forward-compatibility guard for the
// moment someone inevitably adds a `policy_edit` or `prompt_edit` tool.
forbid(
    principal == Agent::"reflector",
    action,
    resource
)
when {
    resource has resource_type &&
    (resource.resource_type == "agent_config" ||
     resource.resource_type == "policy" ||
     resource.resource_type == "prompt")
};
