//! Static per-model pricing table used to compute `est_cost` at record
//! time.
//!
//! Numbers are USD per million tokens, pulled from the OpenRouter
//! catalog (`GET /api/v1/models`) on 2026-04-20 for the models we sweep
//! against. Native Anthropic prices come from Anthropic's public rate
//! card. Ollama / local / mock runs cost $0.
//!
//! Prices rot. Refresh this table when any of:
//!   - You add a new model to the sweep.
//!   - OpenRouter publishes a pricing change (check their model catalog).
//!   - A run's `est_cost` diverges materially from the OpenRouter
//!     `usage.cost` captured in parallel by the generation-id probe.
//!
//! This table is intentionally *estimation*, not authoritative billing.
//! The paired `scripts/fetch-openrouter-costs.py` post-hoc script
//! queries `GET /api/v1/generation?id=<id>` for actual billed costs
//! when the operator wants dollar-perfect numbers.
//!
//! Lookup is model-id prefix match so Anthropic snapshots
//! (`claude-opus-4-5-20250929`) match `claude-opus-4`, and the
//! OpenRouter-prefixed versions (`anthropic/claude-opus-4`) match too.

/// Prompt $/1M, completion $/1M.
pub struct Pricing {
    pub prompt_per_mtok: f64,
    pub completion_per_mtok: f64,
}

/// (prefix, prompt $/1M, completion $/1M). Longest-prefix wins, so
/// `anthropic/claude-haiku-4-5` matches before the broader
/// `anthropic/claude-haiku`.
const TABLE: &[(&str, f64, f64)] = &[
    // --- Anthropic direct ---
    ("claude-opus-4-7",                 15.00, 75.00),
    ("claude-opus-4-5",                 15.00, 75.00),
    ("claude-opus",                     15.00, 75.00),
    ("claude-sonnet-4-6",                3.00, 15.00),
    ("claude-sonnet-4",                  3.00, 15.00),
    ("claude-sonnet",                    3.00, 15.00),
    ("claude-haiku-4-5",                 1.00,  5.00),
    ("claude-haiku",                     1.00,  5.00),
    // --- OpenRouter mirrors ---
    ("anthropic/claude-opus-4-7",       15.00, 75.00),
    ("anthropic/claude-opus-4-5",       15.00, 75.00),
    ("anthropic/claude-opus",           15.00, 75.00),
    ("anthropic/claude-sonnet-4-6",      3.00, 15.00),
    ("anthropic/claude-sonnet",          3.00, 15.00),
    ("anthropic/claude-haiku-4.5",       1.00,  5.00),
    ("anthropic/claude-haiku",           1.00,  5.00),
    ("openai/gpt-5",                     1.25, 10.00),
    ("openai/gpt-oss-20b",               0.03,  0.14),
    ("google/gemini-2.5-pro",            1.25, 10.00),
    ("deepseek/deepseek-chat-v3.1",      0.15,  0.75),
    ("deepseek/deepseek-chat",           0.15,  0.75),
    ("qwen/qwen3-235b-a22b-2507",        0.07,  0.10),
    ("qwen/qwen3-235b",                  0.07,  0.10),
    ("qwen/qwen3.6-plus",                0.33,  1.95),
    ("xiaomi/mimo-v2-pro",               1.00,  3.00),
    ("minimax/minimax-m2.7",             0.30,  1.20),
    // --- Local / mock: zero cost ---
    ("gemma4",                           0.00,  0.00),
    ("gemma3",                           0.00,  0.00),
    ("qwen3:",                           0.00,  0.00),
    ("mock",                             0.00,  0.00),
];

/// Look up pricing for `model_id`. Falls back to a zero entry so
/// runs that don't match still record a row (with `est_cost=0`).
pub fn price(model_id: &str) -> Pricing {
    let id = model_id.trim().to_ascii_lowercase();
    // Longest-prefix match. O(n*m) but the table is tiny.
    let mut best: Option<(usize, f64, f64)> = None;
    for (prefix, pin, pout) in TABLE {
        let p = prefix.to_ascii_lowercase();
        if id.starts_with(&p) && best.map(|(l, _, _)| p.len() > l).unwrap_or(true) {
            best = Some((p.len(), *pin, *pout));
        }
    }
    match best {
        Some((_, pin, pout)) => Pricing {
            prompt_per_mtok: pin,
            completion_per_mtok: pout,
        },
        None => Pricing {
            prompt_per_mtok: 0.0,
            completion_per_mtok: 0.0,
        },
    }
}

/// Compute estimated USD cost given prompt/completion token counts.
pub fn cost_usd(model_id: &str, prompt_tokens: u32, completion_tokens: u32) -> f64 {
    let p = price(model_id);
    (prompt_tokens as f64 / 1_000_000.0) * p.prompt_per_mtok
        + (completion_tokens as f64 / 1_000_000.0) * p.completion_per_mtok
}

/// Fallback split for providers that only report `total_tokens` (e.g.
/// the mock). A 70/30 prompt/completion heuristic tracks agentic
/// tool-use workloads reasonably well.
pub fn split_70_30(total: u32) -> (u32, u32) {
    let p = (total as f64 * 0.7).round() as u32;
    (p, total.saturating_sub(p))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_and_prefix_match() {
        let p = price("claude-opus-4-7");
        assert_eq!(p.prompt_per_mtok, 15.00);
        let p = price("openai/gpt-5");
        assert_eq!(p.completion_per_mtok, 10.00);
        // Longest prefix wins:
        let p = price("anthropic/claude-haiku-4.5");
        assert_eq!(p.prompt_per_mtok, 1.00);
    }

    #[test]
    fn unknown_is_zero() {
        let p = price("made-up/nothing");
        assert_eq!(p.prompt_per_mtok, 0.0);
    }

    #[test]
    fn computes_cost() {
        // 700k prompt + 300k completion = 1Mtok total, split 70/30 — the
        // numbers in the assertion read naturally as "0.7 of an Mtok at
        // the prompt rate plus 0.3 of an Mtok at the completion rate".
        let c = cost_usd("openai/gpt-oss-20b", 700_000, 300_000);
        assert!((c - (0.7 * 0.03 + 0.3 * 0.14)).abs() < 1e-6);
    }
}
