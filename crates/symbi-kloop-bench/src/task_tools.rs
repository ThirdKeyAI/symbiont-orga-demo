//! Per-task tool registration.
//!
//! Each task's JSON file declares the input data the tools operate on
//! (e.g. shipping rates, link costs, the string to classify). We map
//! `task_id` to a set of tool handlers the `TaskActionExecutor` exposes.
//!
//! Keeping this separate from the tasks themselves (which are data) means
//! adding a task handler is a deliberate code change rather than "the
//! task JSON taught the agent how to read files".

use std::sync::Arc;

use demo_karpathy_loop::{Task, TaskActionExecutor};
use symbi_runtime::reasoning::inference::ToolDefinition;

/// Attach the task-specific tools described by `task` to `executor`.
///
/// Returns the `ToolDefinition`s registered so the harness can merge
/// them into the `LoopConfig.tool_definitions` before calling
/// `runner.run()`. The runtime calls `executor.tool_definitions()` as a
/// fallback when the config has none, but our config is explicit.
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

// ── T1: shipping-rate sort ─────────────────────────────────────────────

fn register_t1(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    // rates: HashMap<item, rate>
    let rates: std::collections::HashMap<String, f64> = task
        .inputs
        .get("rates")
        .and_then(|v| v.as_object())
        .map(|m| {
            m.iter()
                .filter_map(|(k, v)| v.as_f64().map(|r| (k.clone(), r)))
                .collect()
        })
        .unwrap_or_default();

    // rate_lookup({"item":"pen"}) → "0.50"
    let rates_for_lookup = rates.clone();
    let rate_lookup_def = ToolDefinition {
        name: "rate_lookup".into(),
        description: "Return the per-unit shipping rate for a single item.".into(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": {"item": {"type": "string"}},
            "required": ["item"]
        }),
    };
    executor.register_tool(rate_lookup_def.clone(), move |args| {
        let parsed: serde_json::Value = serde_json::from_str(args).map_err(|e| e.to_string())?;
        let item = parsed
            .get("item")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "missing `item`".to_string())?;
        match rates_for_lookup.get(item) {
            Some(r) => Ok(format!("{:.4}", r)),
            None => Err(format!("unknown item '{}'", item)),
        }
    })?;

    // compare({"a":"pen","b":"notebook"}) → "pen<notebook"
    let rates_for_cmp = Arc::new(rates);
    let compare_def = ToolDefinition {
        name: "compare".into(),
        description:
            "Compare shipping rates for two items. Returns '<a>' or '<b>' or 'equal'."
                .into(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"]
        }),
    };
    executor.register_tool(compare_def.clone(), move |args| {
        let parsed: serde_json::Value = serde_json::from_str(args).map_err(|e| e.to_string())?;
        let a = parsed
            .get("a")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "missing `a`".to_string())?;
        let b = parsed
            .get("b")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "missing `b`".to_string())?;
        let ra = rates_for_cmp
            .get(a)
            .copied()
            .ok_or_else(|| format!("unknown item '{a}'"))?;
        let rb = rates_for_cmp
            .get(b)
            .copied()
            .ok_or_else(|| format!("unknown item '{b}'"))?;
        let verdict = if ra < rb {
            a
        } else if ra > rb {
            b
        } else {
            "equal"
        };
        Ok(verdict.to_string())
    })?;

    Ok(vec![rate_lookup_def, compare_def])
}

// ── T2: minimum spanning tree ──────────────────────────────────────────

fn register_t2(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    // links: Vec<{a, b, cost}>.
    #[derive(Clone)]
    struct Link {
        a: String,
        b: String,
        cost: f64,
    }
    let links: Vec<Link> = task
        .inputs
        .get("links")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|l| {
                    Some(Link {
                        a: l.get("a")?.as_str()?.to_string(),
                        b: l.get("b")?.as_str()?.to_string(),
                        cost: l.get("cost")?.as_f64()?,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let links = Arc::new(links);

    // link_cost({"a":"A","b":"B"}) → "4.00" or "none"
    let links_for_cost = links.clone();
    let link_cost_def = ToolDefinition {
        name: "link_cost".into(),
        description: "Return the cost of a direct link between two offices, or 'none'."
            .into(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"]
        }),
    };
    executor.register_tool(link_cost_def.clone(), move |args| {
        let parsed: serde_json::Value = serde_json::from_str(args).map_err(|e| e.to_string())?;
        let a = parsed
            .get("a")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "missing `a`".to_string())?;
        let b = parsed
            .get("b")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "missing `b`".to_string())?;
        for link in links_for_cost.iter() {
            if (link.a == a && link.b == b) || (link.a == b && link.b == a) {
                return Ok(format!("{:.4}", link.cost));
            }
        }
        Ok("none".to_string())
    })?;

    // compare(a_edge, b_edge) by combined-weight heuristic. We cheat here
    // for demo purposes — Kruskal's doesn't strictly need a pairwise
    // compare, but exposing one lets the mock "long" path do lots of
    // redundant comparisons before converging on the MST.
    let compare_def = ToolDefinition {
        name: "compare".into(),
        description:
            "Compare two numeric values. Returns '<' if a < b, '>' if a > b, 'equal'."
                .into(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"]
        }),
    };
    executor.register_tool(compare_def.clone(), move |args| {
        let parsed: serde_json::Value = serde_json::from_str(args).map_err(|e| e.to_string())?;
        let a = parsed
            .get("a")
            .and_then(|v| v.as_f64())
            .ok_or_else(|| "missing `a`".to_string())?;
        let b = parsed
            .get("b")
            .and_then(|v| v.as_f64())
            .ok_or_else(|| "missing `b`".to_string())?;
        Ok(if a < b {
            "<".to_string()
        } else if a > b {
            ">".to_string()
        } else {
            "equal".to_string()
        })
    })?;

    Ok(vec![link_cost_def, compare_def])
}

// ── T3: string classifier ──────────────────────────────────────────────

fn register_t3(
    task: &Task,
    executor: &mut TaskActionExecutor,
) -> anyhow::Result<Vec<ToolDefinition>> {
    let s = task
        .inputs
        .get("string")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    let s = Arc::new(s);

    let read_input_def = ToolDefinition {
        name: "read_input".into(),
        description: "Return the input string to classify.".into(),
        parameters: serde_json::json!({"type": "object"}),
    };
    {
        let s = s.clone();
        executor.register_tool(read_input_def.clone(), move |_args| Ok((*s).clone()))?;
    }

    let has_at_sign_def = ToolDefinition {
        name: "has_at_sign".into(),
        description: "Return 'true' iff the input string contains '@'.".into(),
        parameters: serde_json::json!({"type": "object"}),
    };
    {
        let s = s.clone();
        executor.register_tool(has_at_sign_def.clone(), move |_args| {
            Ok(s.contains('@').to_string())
        })?;
    }

    let has_digit_run_def = ToolDefinition {
        name: "has_digit_run".into(),
        description: "Return 'true' iff the input contains 7+ consecutive digits.".into(),
        parameters: serde_json::json!({"type": "object"}),
    };
    {
        let s = s.clone();
        executor.register_tool(has_digit_run_def.clone(), move |_args| {
            let longest = s
                .chars()
                .fold((0usize, 0usize), |(cur, max), c| {
                    if c.is_ascii_digit() {
                        (cur + 1, max.max(cur + 1))
                    } else {
                        (0, max)
                    }
                })
                .1;
            Ok((longest >= 7).to_string())
        })?;
    }

    let has_scheme_def = ToolDefinition {
        name: "has_scheme".into(),
        description: "Return 'true' iff the input starts with 'http://' or 'https://'."
            .into(),
        parameters: serde_json::json!({"type": "object"}),
    };
    {
        let s = s.clone();
        executor.register_tool(has_scheme_def.clone(), move |_args| {
            Ok((s.starts_with("http://") || s.starts_with("https://")).to_string())
        })?;
    }

    Ok(vec![
        read_input_def,
        has_at_sign_def,
        has_digit_run_def,
        has_scheme_def,
    ])
}
