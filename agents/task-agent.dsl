// Task agent — participant in the Karpathy loop.
//
// Read-only on the knowledge store (via `recall_knowledge`). Writes
// are the reflector's exclusive privilege. The tool list here is
// informational; the harness binds the concrete tool set from the
// task definition at run time.

metadata {
    version "0.2.0"
    author "symbiont-karpathy-loop"
    description "Solves a single benchmark task per invocation, reading any procedures the reflector previously wrote."
}

with {
    sandbox docker
    timeout 60.seconds
}

capabilities {
    // Loop-level
    tool "answer"
    tool "recall_knowledge"

    // T1 — K8s deployment triage
    tool "pod_status"
    tool "container_exit"
    tool "pod_events"
    tool "deployment_manifest"
    tool "recent_logs"
    tool "memory_metric"
    tool "image_registry_check"

    // T2 — support ticket classifier
    tool "ticket_title"
    tool "ticket_body"
    tool "product_area"
    tool "search_similar"
    tool "read_runbook"

    // T3 — dependency upgrade safety review
    tool "from_version"
    tool "to_version"
    tool "changelog_summary"
    tool "breaking_changes_flag"
    tool "usage_count"
    tool "run_tests"

    // T4 — rustc error classifier
    tool "error_code_line"
    tool "error_text"
    tool "source_snippet"
    tool "search_rustc_docs"
    tool "similar_errors"
}
