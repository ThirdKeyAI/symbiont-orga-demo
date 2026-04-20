#!/usr/bin/env bash
# Full OpenRouter model sweep for the public README.
#
# Requires OPENROUTER_API_KEY in .env (or the environment). Each model
# gets its own data/$tag/runs.db, journals-$tag/, and demo-output
# artefacts so the existing opus/sonnet/gemma4 results stay intact.
#
# Usage:
#   scripts/run-openrouter-sweep.sh              # all 9 models, 3 iters
#   scripts/run-openrouter-sweep.sh 5            # all 9 models, 5 iters
#   ONLY=gpt5 scripts/run-openrouter-sweep.sh    # one model by tag

set -euo pipefail

ITERATIONS=${1:-3}
ONLY=${ONLY:-}

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . .env
    set +a
fi

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "error: OPENROUTER_API_KEY not set (add to .env)" >&2
    exit 1
fi

BIN=target/release/symbi-kloop-bench
if [[ ! -x "$BIN" ]]; then
    echo "→ building symbi-kloop-bench (release)"
    cargo build --release --quiet -p symbi-kloop-bench --message-format=short 2>&1 |
        grep -v '^warning:' || true
fi

# tag                model-id
MODELS=(
    "gpt5:openai/gpt-5"
    "gemini25pro:google/gemini-2.5-pro"
    "haiku45:anthropic/claude-haiku-4.5"
    "deepseekv31:deepseek/deepseek-chat-v3.1"
    "qwen3-235b:qwen/qwen3-235b-a22b-2507"
    "mimo-v2-pro:xiaomi/mimo-v2-pro"
    "minimax-m27:minimax/minimax-m2.7"
    "gpt-oss-20b:openai/gpt-oss-20b"
    "qwen36-plus:qwen/qwen3.6-plus"
)

for entry in "${MODELS[@]}"; do
    tag="${entry%%:*}"
    model="${entry#*:}"

    if [[ -n "$ONLY" && "$ONLY" != "$tag" ]]; then
        continue
    fi

    echo
    echo "============================================================"
    echo "  $tag — $model"
    echo "============================================================"

    mkdir -p "data/$tag" "journals-$tag"
    rm -f "data/$tag/"*.db "journals-$tag/"*.json
    : > "demo-output/$tag.log"

    # OPENROUTER_MODEL is what LlmClient::from_env() reads for OpenRouter.
    start=$(date +%s)
    OPENROUTER_MODEL="$model" \
    "$BIN" \
        --db "data/$tag/runs.db" \
        --journals-dir "journals-$tag" \
        --provider cloud \
        demo --iterations "$ITERATIONS" \
        >> "demo-output/$tag.log" 2>&1 || {
            echo "  ! $tag failed (rc=$?), continuing — see demo-output/$tag.log"
            continue
        }
    elapsed=$(( $(date +%s) - start ))

    "$BIN" --db "data/$tag/runs.db" dashboard --limit 40 \
        > "demo-output/$tag-dashboard.txt"
    "$BIN" --db "data/$tag/runs.db" report \
        --out "demo-output/run-$tag.md"

    tail -1 "demo-output/$tag.log"
    echo "  → ${elapsed}s wall, artefacts in demo-output/"
done

echo
echo "→ sweep complete. See demo-output/*-dashboard.txt for per-model breakdowns."
