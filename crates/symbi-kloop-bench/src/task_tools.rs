//! Per-task tool registration.
//!
//! Each task's JSON file declares the input data the tools operate on
//! (deployment signals, a ticket body, a dependency-bump blob, …). We
//! map `task_id` to a set of handlers the `TaskActionExecutor` exposes.
//!
//! Keeping this separate from the task JSON means adding a new tool is
//! a deliberate code change — a task JSON can describe its inputs but
//! cannot mint new tool surface area on its own.

use std::sync::Arc;

use demo_karpathy_loop::{Task, TaskActionExecutor};
use symbi_runtime::reasoning::inference::ToolDefinition;

/// Attach the task-specific tools described by `task` to `executor`.
///
/// Returns the `ToolDefinition`s registered so the harness can merge
/// them into `LoopConfig.tool_definitions` before calling `runner.run()`.
pub fn register_for_task(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    match task.id.as_str() {
        "T1" => register_t1(task, executor),
        "T2" => register_t2(task, executor),
        "T3" => register_t3(task, executor),
        other => anyhow::bail!("no tool handlers registered for task id '{}'", other),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────

fn no_arg_tool(name: &str, description: &str) -> ToolDefinition {
    ToolDefinition {
        name: name.to_string(),
        description: description.to_string(),
        parameters: serde_json::json!({"type": "object"}),
    }
}

fn single_string_arg_tool(name: &str, description: &str, arg_name: &str) -> ToolDefinition {
    ToolDefinition {
        name: name.to_string(),
        description: description.to_string(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": { arg_name: {"type": "string"} },
            "required": [arg_name]
        }),
    }
}

fn json_at<'a>(v: &'a serde_json::Value, path: &[&str]) -> Option<&'a serde_json::Value> {
    let mut cur = v;
    for seg in path {
        cur = cur.get(seg)?;
    }
    Some(cur)
}

fn render_value(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Array(arr) => arr
            .iter()
            .map(|x| match x {
                serde_json::Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect::<Vec<_>>()
            .join("\n"),
        other => serde_json::to_string_pretty(other).unwrap_or_else(|_| other.to_string()),
    }
}

// ── T1: K8s deployment triage ─────────────────────────────────────────

fn register_t1(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    let signals = task
        .inputs
        .get("signals")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    let s = Arc::new(signals);

    let mut defs = Vec::new();

    // pod_status() → single string
    let pod_status_def = no_arg_tool(
        "pod_status",
        "Return the pod's current high-level phase (Running / Pending / CrashLoopBackOff / ...).",
    );
    {
        let s = s.clone();
        executor.register_tool(pod_status_def.clone(), move |_| {
            json_at(&s, &["pod_status"])
                .map(render_value)
                .ok_or_else(|| "no pod_status in signals".to_string())
        })?;
    }
    defs.push(pod_status_def);

    // container_exit() → "<code> <reason>"
    let container_exit_def = no_arg_tool(
        "container_exit",
        "Return `<exit_code> <exit_reason>` for the last container termination.",
    );
    {
        let s = s.clone();
        executor.register_tool(container_exit_def.clone(), move |_| {
            let code = json_at(&s, &["container_exit_code"])
                .map(|v| v.to_string())
                .unwrap_or_else(|| "?".into());
            let reason = json_at(&s, &["container_exit_reason"])
                .map(render_value)
                .unwrap_or_else(|| "?".into());
            Ok(format!("{code} {reason}"))
        })?;
    }
    defs.push(container_exit_def);

    let pod_events_def = no_arg_tool(
        "pod_events",
        "Return the last warning events on the pod, one per line.",
    );
    {
        let s = s.clone();
        executor.register_tool(pod_events_def.clone(), move |_| {
            Ok(json_at(&s, &["pod_events"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(pod_events_def);

    let manifest_def = no_arg_tool(
        "deployment_manifest",
        "Return a JSON dump of the deployment manifest.",
    );
    {
        let s = s.clone();
        executor.register_tool(manifest_def.clone(), move |_| {
            Ok(json_at(&s, &["deployment_manifest"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(manifest_def);

    let logs_def = no_arg_tool(
        "recent_logs",
        "Return the last stderr lines, one per line.",
    );
    {
        let s = s.clone();
        executor.register_tool(logs_def.clone(), move |_| {
            Ok(json_at(&s, &["recent_logs"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(logs_def);

    let memory_def = no_arg_tool(
        "memory_metric",
        "Return observed memory usage as a percent of the request.",
    );
    {
        let s = s.clone();
        executor.register_tool(memory_def.clone(), move |_| {
            Ok(json_at(&s, &["memory_usage_pct"])
                .map(|v| v.to_string())
                .unwrap_or_default())
        })?;
    }
    defs.push(memory_def);

    let registry_def = no_arg_tool(
        "image_registry_check",
        "Return `true`/`false` — whether the image tag exists in the registry.",
    );
    {
        let s = s.clone();
        executor.register_tool(registry_def.clone(), move |_| {
            Ok(json_at(&s, &["image_registry_exists"])
                .map(|v| v.to_string())
                .unwrap_or_default())
        })?;
    }
    defs.push(registry_def);

    Ok(defs)
}

// ── T2: support ticket classifier ─────────────────────────────────────

fn register_t2(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    let ticket = task
        .inputs
        .get("ticket")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    let t = Arc::new(ticket);

    let mut defs = Vec::new();

    let title_def = no_arg_tool("ticket_title", "Return the ticket's title text.");
    {
        let t = t.clone();
        executor.register_tool(title_def.clone(), move |_| {
            Ok(json_at(&t, &["title"]).map(render_value).unwrap_or_default())
        })?;
    }
    defs.push(title_def);

    let body_def = no_arg_tool("ticket_body", "Return the full body of the ticket.");
    {
        let t = t.clone();
        executor.register_tool(body_def.clone(), move |_| {
            Ok(json_at(&t, &["body"]).map(render_value).unwrap_or_default())
        })?;
    }
    defs.push(body_def);

    let area_def = no_arg_tool(
        "product_area",
        "Return the product area the user tagged; may be empty.",
    );
    {
        let t = t.clone();
        executor.register_tool(area_def.clone(), move |_| {
            Ok(json_at(&t, &["product_area"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(area_def);

    let search_def = no_arg_tool(
        "search_similar",
        "Free-text search over historical tickets. Returns up to 3 one-line matches.",
    );
    {
        let t = t.clone();
        executor.register_tool(search_def.clone(), move |_| {
            Ok(json_at(&t, &["similar_titles"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(search_def);

    let runbook_def = no_arg_tool(
        "read_runbook",
        "Return the short runbook reminder on how to classify.",
    );
    {
        let t = t.clone();
        executor.register_tool(runbook_def.clone(), move |_| {
            Ok(json_at(&t, &["runbook_note"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(runbook_def);

    Ok(defs)
}

// ── T3: dependency upgrade safety ─────────────────────────────────────

fn register_t3(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    let bump = task
        .inputs
        .get("bump")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    let b = Arc::new(bump);

    let mut defs = Vec::new();

    let from_def = no_arg_tool("from_version", "Return the current version string.");
    {
        let b = b.clone();
        executor.register_tool(from_def.clone(), move |_| {
            Ok(json_at(&b, &["from_version"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(from_def);

    let to_def = no_arg_tool("to_version", "Return the proposed version string.");
    {
        let b = b.clone();
        executor.register_tool(to_def.clone(), move |_| {
            Ok(json_at(&b, &["to_version"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(to_def);

    let changelog_def = no_arg_tool(
        "changelog_summary",
        "Return the changelog excerpt (up to ~3 lines).",
    );
    {
        let b = b.clone();
        executor.register_tool(changelog_def.clone(), move |_| {
            Ok(json_at(&b, &["changelog_summary"])
                .map(render_value)
                .unwrap_or_default())
        })?;
    }
    defs.push(changelog_def);

    let flag_def = no_arg_tool(
        "breaking_changes_flag",
        "Return `true` if the changelog calls out breaking changes, else `false`.",
    );
    {
        let b = b.clone();
        executor.register_tool(flag_def.clone(), move |_| {
            Ok(json_at(&b, &["breaking_changes_flag"])
                .map(|v| v.to_string())
                .unwrap_or_else(|| "false".to_string()))
        })?;
    }
    defs.push(flag_def);

    let usage_def = single_string_arg_tool(
        "usage_count",
        "Return how many times a given API symbol (e.g. `Value::as_f64`) is used in this repo.",
        "api_name",
    );
    {
        let b = b.clone();
        executor.register_tool(usage_def.clone(), move |args| {
            let parsed: serde_json::Value = serde_json::from_str(args).unwrap_or_default();
            let api = parsed
                .get("api_name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "missing `api_name`".to_string())?;
            let count = json_at(&b, &["usage_counts", api])
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            Ok(count.to_string())
        })?;
    }
    defs.push(usage_def);

    let ci_def = no_arg_tool(
        "run_tests",
        "Return `pass` or `fail` based on the latest CI run.",
    );
    {
        let b = b.clone();
        executor.register_tool(ci_def.clone(), move |_| {
            Ok(json_at(&b, &["ci_status"])
                .map(render_value)
                .unwrap_or_else(|| "unknown".to_string()))
        })?;
    }
    defs.push(ci_def);

    Ok(defs)
}
