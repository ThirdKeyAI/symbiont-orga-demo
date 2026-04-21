#!/usr/bin/env bash
# T5 cross-pairing matrix — can a premium reflector lift a cheap task agent?
#
# Fixes the task agent at `openai/gpt-oss-20b` (the v2 sweep's single
# clearest loop-closer) and varies the reflector across four arms:
#   1. none          — negative control, no learning signal
#   2. gpt-oss-20b   — self-reflection baseline (what v2 actually ran)
#   3. haiku-4.5     — mid-premium teacher
#   4. gpt-5         — premium teacher
#
# Each arm runs 5 iterations of T5 alone so we can watch the iteration
# count across n=1..5 on a single task designed for a long cold path
# with a clean learnable shortcut.
#
# Artefacts per arm live under data/t5-<arm>/ and journals-t5-<arm>/
# so nothing collides with the full sweeps.

set -euo pipefail

ITERATIONS=${1:-5}

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
    cargo build --release --quiet -p symbi-kloop-bench --message-format=short
fi

# Broadcast trace labels so observability dashboards can split by arm.
export OPENROUTER_USER="${OPENROUTER_USER:-symbiont-karpathy-loop}"

# Arm spec: `<arm_tag>:<reflector_model_or_empty>` — empty means
# pass --no-reflector (no learning signal). Task agent is always
# openai/gpt-oss-20b.
ARMS=(
    "none:"
    "self:openai/gpt-oss-20b"
    "haiku:anthropic/claude-haiku-4.5"
    "gpt5:openai/gpt-5"
)

TASK_MODEL="openai/gpt-oss-20b"

for entry in "${ARMS[@]}"; do
    arm="${entry%%:*}"
    reflect="${entry#*:}"
    tag="t5-${arm}"

    echo
    echo "============================================================"
    if [[ -z "$reflect" ]]; then
        echo "  $tag — task=$TASK_MODEL, reflector=NONE"
    else
        echo "  $tag — task=$TASK_MODEL, reflector=$reflect"
    fi
    echo "============================================================"

    mkdir -p "data/$tag" "journals-$tag"
    rm -f "data/$tag/"*.db "journals-$tag/"*.json "journals-$tag/"*.jsonl
    : > "demo-output/$tag.log"

    start=$(date +%s)
    export OPENROUTER_MODEL_TASK="$TASK_MODEL"
    export OPENROUTER_TRACE_ENV="v3-t5-$arm"
    if [[ -z "$reflect" ]]; then
        unset OPENROUTER_MODEL_REFLECT
        export OPENROUTER_MODEL="$TASK_MODEL"
        extra=(--no-reflector)
    else
        export OPENROUTER_MODEL_REFLECT="$reflect"
        export OPENROUTER_MODEL="$TASK_MODEL"
        extra=()
    fi

    "$BIN" \
        --db "data/$tag/runs.db" \
        --journals-dir "journals-$tag" \
        --provider openrouter \
        demo --iterations "$ITERATIONS" --only T5 "${extra[@]}" \
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
echo "→ T5 matrix complete."
