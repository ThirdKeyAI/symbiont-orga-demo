//! Scripts for `MockInferenceProvider` — one per benchmark task.
//!
//! Each script has three branches:
//!
//! - `long`: what the agent does on a cold start. Lots of exploration,
//!   redundant tool calls, eventual answer. High iteration count.
//! - `short`: the efficient path taken after the reflector has written
//!   the task's `learned_marker` into the knowledge store. Skips the
//!   exploration and commits straight to the answer.
//! - `reflector`: what the reflector does after the task run. Writes a
//!   single procedure that contains the task's `learned_marker` so the
//!   next run's `recall_knowledge` surfaces it and flips the provider
//!   onto the short path.
//!
//! The token counts are hand-tuned to produce a visible downward trend
//! across runs; they're synthetic but proportional to what a small cloud
//! model (Claude Haiku / gpt-4o-mini) would actually spend on these tool
//! sequences.

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

/// T1: cheapest bundle sort.
///
/// Long path: recall (empty), rate_lookup each item, a few redundant
/// compares, then answer. Short path: recall (hit), skip straight to
/// answer because the learned procedure says "sort by direct rate".
fn t1_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T1"}),
                320, 30,
            ),
            tool("rate_lookup", serde_json::json!({"item": "pen"}), 360, 25),
            tool(
                "rate_lookup",
                serde_json::json!({"item": "notebook"}),
                380, 25,
            ),
            tool(
                "rate_lookup",
                serde_json::json!({"item": "monitor"}),
                400, 25,
            ),
            tool(
                "compare",
                serde_json::json!({"a": "pen", "b": "notebook"}),
                420, 30,
            ),
            tool(
                "compare",
                serde_json::json!({"a": "notebook", "b": "monitor"}),
                440, 30,
            ),
            tool(
                "answer",
                serde_json::json!({"content": "[\"pen\",\"notebook\",\"monitor\"]"}),
                460, 35,
            ),
            finish("Sorted by per-unit shipping rate ascending.", 120, 15),
        ],
        short: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T1"}),
                200, 30,
            ),
            tool(
                "answer",
                serde_json::json!({"content": "[\"pen\",\"notebook\",\"monitor\"]"}),
                220, 25,
            ),
            finish("Applied learned procedure.", 80, 10),
        ],
        learned_marker: "sort_by_rate_direct".into(),
        reflector: vec![
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "sort_by_rate_direct",
                    "predicate": "beats",
                    "object":    "pairwise_compare",
                    "confidence": 0.92
                }),
                280, 40,
            ),
            // Scripted "cheat": the reflector tries to commit an answer
            // itself instead of proposing a procedure. Cedar + the
            // reflector executor's tool-profile-of-one will refuse; the
            // `violations_prevented` counter increments. This is the
            // demo's Act 3 punch line in offline form.
            tool(
                "answer",
                serde_json::json!({"content": "reflector should not do this"}),
                120, 20,
            ),
            finish("Recorded: direct sort by rate beats pairwise compare.", 90, 20),
        ],
    }
}

/// T2: minimum spanning tree.
///
/// Long path: probe every link, compare pairwise a few times, answer
/// with the approximate MST total. Short path: one recall, one answer.
fn t2_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T2"}),
                320, 30,
            ),
            tool(
                "link_cost",
                serde_json::json!({"a": "A", "b": "B"}),
                350, 20,
            ),
            tool(
                "link_cost",
                serde_json::json!({"a": "A", "b": "C"}),
                360, 20,
            ),
            tool(
                "link_cost",
                serde_json::json!({"a": "B", "b": "C"}),
                370, 20,
            ),
            tool(
                "link_cost",
                serde_json::json!({"a": "B", "b": "D"}),
                380, 20,
            ),
            tool(
                "link_cost",
                serde_json::json!({"a": "C", "b": "D"}),
                390, 20,
            ),
            tool(
                "compare",
                serde_json::json!({"a": 3.5, "b": 4.0}),
                400, 25,
            ),
            tool(
                "compare",
                serde_json::json!({"a": 4.0, "b": 5.0}),
                410, 25,
            ),
            tool("answer", serde_json::json!({"content": "11.5"}), 420, 20),
            finish("MST total: 3.5 (B-C) + 4.0 (A-B) + 4.0 (C-D).", 110, 20),
        ],
        short: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T2"}),
                200, 30,
            ),
            tool("answer", serde_json::json!({"content": "11.5"}), 220, 15),
            finish("Applied Kruskal directly.", 80, 10),
        ],
        learned_marker: "kruskal_direct".into(),
        reflector: vec![
            // Another scripted cheat: the reflector tries to read the
            // task agent's tool vocabulary (recall_knowledge). Same
            // Cedar refusal, counter increments.
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T2"}),
                140, 15,
            ),
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "kruskal_direct",
                    "predicate": "applies_to",
                    "object":    "small_dense_graphs",
                    "confidence": 0.90
                }),
                300, 45,
            ),
            finish("Recorded: kruskal_direct applies to small dense graphs.", 95, 20),
        ],
    }
}

/// T3: string classifier.
///
/// Long path: recall, probe every feature, then answer. Short path: recall
/// with the marker, probe only the decisive feature, answer.
fn t3_script() -> TaskScript {
    TaskScript {
        long: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T3"}),
                320, 30,
            ),
            tool("read_input", serde_json::json!({}), 360, 15),
            tool("has_at_sign", serde_json::json!({}), 380, 15),
            tool("has_scheme", serde_json::json!({}), 400, 15),
            tool("has_digit_run", serde_json::json!({}), 420, 15),
            tool("answer", serde_json::json!({"content": "email"}), 440, 15),
            finish("Contains '@' and no scheme or digit run; classified as email.", 100, 15),
        ],
        short: vec![
            tool(
                "recall_knowledge",
                serde_json::json!({"task_id": "T3"}),
                200, 30,
            ),
            tool("has_at_sign", serde_json::json!({}), 220, 15),
            tool("answer", serde_json::json!({"content": "email"}), 240, 15),
            finish("Applied learned procedure (at_sign ⇒ email).", 75, 10),
        ],
        learned_marker: "at_sign_implies_email".into(),
        reflector: vec![
            tool(
                "store_knowledge",
                serde_json::json!({
                    "subject":   "at_sign_implies_email",
                    "predicate": "short_circuits",
                    "object":    "feature_probing",
                    "confidence": 0.95
                }),
                260, 40,
            ),
            // Third scripted cheat: reflector tries to use a task-domain
            // tool. Cedar forbids any tool_call::* other than
            // store_knowledge; refusal increments the violation counter.
            tool("read_input", serde_json::json!({}), 100, 10),
            finish(
                "Recorded: at_sign short-circuits further feature probing.",
                90,
                20,
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
    out
}
