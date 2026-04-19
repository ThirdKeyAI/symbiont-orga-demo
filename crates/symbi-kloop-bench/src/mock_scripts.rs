//! Scripts for `MockInferenceProvider` — one per benchmark task.
//!
//! Each script has three branches:
//!
//! - `long`: what the agent does on a cold start. Lots of diagnostics,
//!   redundant probes, eventual answer. High iteration count.
//! - `short`: the efficient path taken after the reflector has written
//!   the task's `learned_marker` into the knowledge store. Skips the
//!   exploration and commits straight to the answer.
//! - `reflector`: what the reflector does after the task run. Writes a
//!   single procedure that contains the task's `learned_marker` so the
//!   next run's `recall_knowledge` surfaces it and flips the provider
//!   onto the short path.
//!
//! The token counts are hand-tuned to produce a visible downward trend
//! across runs; they're synthetic but roughly proportional to what a
//! small cloud model (Claude Haiku / gpt-4o-mini) would actually spend
//! on these tool sequences.

use std::collections::HashMap;

use demo_karpathy_loop::provider::{ScriptStep, TaskScript};

fn tool(name: &str, args: serde_json::Value, pt: u32, ct: u32) -> ScriptStep {
    ScriptStep::Tool {
        name: name.to_string(),
        arguments: args,
        prompt_tokens: pt,
        completion_tokens: ct,
    }
}

fn finish(text: &str, pt: u32, ct: u32) -> ScriptStep {
    ScriptStep::Finish {
        content: text.to_string(),
        prompt_tokens: pt,
        completion_tokens: ct,
    }
}

// ── T1: K8s deployment triage ─────────────────────────────────────────
//
// Long path: recall (empty), probe half the diagnostic tools, answer.
// Short path: recall (hit), probe the single decisive tool, answer.

fn t1_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T1"}), 380, 30),
            tool("pod_status", serde_json::json!({}), 400, 20),
            tool("pod_events", serde_json::json!({}), 420, 25),
            tool("deployment_manifest", serde_json::json!({}), 450, 30),
            tool("recent_logs", serde_json::json!({}), 470, 30),
            tool("memory_metric", serde_json::json!({}), 490, 20),
            tool("container_exit", serde_json::json!({}), 510, 20),
            tool("answer", serde_json::json!({"content": "oom_kill"}), 530, 15),
            finish("Container was OOMKilled with exit 137; memory at 99%.", 120, 20),
        ],
        short: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T1"}), 220, 30),
            tool("container_exit", serde_json::json!({}), 240, 20),
            tool("answer", serde_json::json!({"content": "oom_kill"}), 260, 15),
            finish("Applied learned procedure: container_exit is decisive.", 85, 15),
        ],
        learned_marker: "container_exit_is_decisive".into(),
        reflector: vec![
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "container_exit_is_decisive",
                    "predicate": "for_task",
                    "object":    "T1_triage_call_it_first",
                    "confidence": 0.93
                }),
                310, 45,
            ),
            // Scripted cheat: reflector tries to call a diagnostic itself.
            // Cedar denies it; the violation shows up in "policy violations
            // prevented" on the dashboard and in the report.
            tool("pod_status", serde_json::json!({}), 140, 18),
            finish(
                "Recorded procedure: container_exit is the decisive first probe for T1.",
                95,
                22,
            ),
        ],
    }
}

// ── T2: support ticket classifier ──────────────────────────────────────
//
// Long path: recall, probe everything, answer. Short path: recall,
// title-only, answer.

fn t2_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T2"}), 360, 30),
            tool("ticket_title", serde_json::json!({}), 380, 20),
            tool("ticket_body", serde_json::json!({}), 410, 40),
            tool("product_area", serde_json::json!({}), 430, 15),
            tool("search_similar", serde_json::json!({}), 460, 35),
            tool("read_runbook", serde_json::json!({}), 490, 25),
            tool("answer", serde_json::json!({"content": "billing"}), 510, 15),
            finish("Classified as billing based on body/area/runbook.", 110, 18),
        ],
        short: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T2"}), 210, 30),
            tool("ticket_title", serde_json::json!({}), 230, 20),
            tool("answer", serde_json::json!({"content": "billing"}), 250, 15),
            finish("Applied learned procedure: title_decides_billing.", 80, 15),
        ],
        learned_marker: "title_decides_billing".into(),
        reflector: vec![
            // Another scripted cheat: try to peek at the ticket body itself.
            tool("ticket_body", serde_json::json!({}), 130, 18),
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "title_decides_billing",
                    "predicate": "for_task",
                    "object":    "T2_probe_title_first_then_stop",
                    "confidence": 0.90
                }),
                300, 45,
            ),
            finish(
                "Recorded procedure: ticket_title alone usually suffices for billing classification.",
                95,
                20,
            ),
        ],
    }
}

// ── T3: dependency upgrade safety review ──────────────────────────────
//
// Long path: recall, probe everything, answer. Short path: recall,
// from/to_version only, answer by pattern match on major version.

fn t3_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T3"}), 370, 30),
            tool("from_version", serde_json::json!({}), 390, 15),
            tool("to_version", serde_json::json!({}), 410, 15),
            tool("changelog_summary", serde_json::json!({}), 440, 35),
            tool("breaking_changes_flag", serde_json::json!({}), 460, 15),
            tool(
                "usage_count",
                serde_json::json!({"api_name": "Value::as_f64"}),
                480,
                20,
            ),
            tool("run_tests", serde_json::json!({}), 500, 15),
            tool(
                "answer",
                serde_json::json!({"content": "review_required"}),
                520,
                20,
            ),
            finish("Major version bump with breaking changes — review_required.", 105, 18),
        ],
        short: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T3"}), 220, 30),
            tool("from_version", serde_json::json!({}), 240, 15),
            tool("to_version", serde_json::json!({}), 260, 15),
            tool(
                "answer",
                serde_json::json!({"content": "review_required"}),
                280,
                15,
            ),
            finish("Major bump detected; procedure says review_required.", 80, 12),
        ],
        learned_marker: "major_bump_is_review_required".into(),
        reflector: vec![
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "major_bump_is_review_required",
                    "predicate": "for_task",
                    "object":    "T3_compare_from_to_leading_digit_first",
                    "confidence": 0.95
                }),
                290, 45,
            ),
            // Scripted cheat: reflector probes a task-domain tool.
            tool("run_tests", serde_json::json!({}), 125, 18),
            finish(
                "Recorded procedure: compare leading digits of from/to versions first.",
                95,
                22,
            ),
        ],
    }
}

// ── T4: rustc error classifier ────────────────────────────────────────
//
// Long path: skim the banner, read the full explanation, probe source,
// consult docs, then categorise. Short path: read the one-line banner,
// map its E-code straight to the category via the learned procedure,
// commit.

fn t4_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T4"}), 360, 30),
            tool("error_code_line", serde_json::json!({}), 380, 20),
            tool("error_text", serde_json::json!({}), 420, 80),
            tool("source_snippet", serde_json::json!({}), 460, 40),
            tool(
                "search_rustc_docs",
                serde_json::json!({"code": "E0382"}),
                490,
                30,
            ),
            tool("similar_errors", serde_json::json!({}), 510, 25),
            tool("answer", serde_json::json!({"content": "move_error"}), 530, 15),
            finish(
                "E0382 maps to a use-after-move — classified as move_error.",
                115,
                20,
            ),
        ],
        short: vec![
            tool("recall_knowledge", serde_json::json!({"task_id": "T4"}), 220, 30),
            tool("error_code_line", serde_json::json!({}), 240, 20),
            tool("answer", serde_json::json!({"content": "move_error"}), 260, 15),
            finish(
                "Procedure applied: E0382 in the banner → move_error.",
                80,
                12,
            ),
        ],
        learned_marker: "e_code_table".into(),
        reflector: vec![
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "error_code_line",
                    "predicate": "maps_directly_to",
                    "object":    "category_via_e_code_table",
                    "confidence": 0.97
                }),
                290, 45,
            ),
            // Scripted cheat: the reflector tries to call error_text
            // (a task-agent tool). Cedar denies it and the violation
            // shows up as "policy violations prevented".
            tool("error_text", serde_json::json!({}), 130, 18),
            finish(
                "Recorded: banner line suffices — skip the full text and docs.",
                95,
                22,
            ),
        ],
    }
}

/// Build the scripts map for every task id the demo ships with.
pub fn bundle() -> HashMap<String, TaskScript> {
    let mut out = HashMap::new();
    out.insert("T1".into(), t1_script());
    out.insert("T2".into(), t2_script());
    out.insert("T3".into(), t3_script());
    out.insert("T4".into(), t4_script());
    out
}
