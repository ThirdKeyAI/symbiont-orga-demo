// Task agent — participant in the Karpathy loop.
//
// Read-only on the knowledge store (via `recall_knowledge`). Writes
// are the reflector's exclusive privilege. The tool list here is
// informational; the harness binds the concrete tool set from the
// task definition at run time.

metadata {
    version "0.1.0"
    author "symbiont-karpathy-loop"
    description "Solves a single benchmark task per invocation, reading any procedures the reflector previously wrote."
}

with {
    sandbox docker
    timeout 60.seconds
}

capabilities {
    tool "answer"
    tool "recall_knowledge"
    tool "rate_lookup"
    tool "compare"
    tool "link_cost"
    tool "read_input"
    tool "has_at_sign"
    tool "has_digit_run"
    tool "has_scheme"
}
