#!/usr/bin/env bash
# Full OpenRouter model sweep for the public README.
#
# Requires OPENROUTER_API_KEY in .env (or the environment). Each model
# gets its own data/$tag{,-adv}/runs.db, journals-$tag{,-adv}/, and
# demo-output artefacts so other runs stay intact.
#
# Usage:
#   scripts/run-openrouter-sweep.sh              # all 9, 3 iters, default reflector
#   scripts/run-openrouter-sweep.sh 5            # all 9, 5 iters
#   ONLY=gpt5 scripts/run-openrouter-sweep.sh    # one model by tag
#   ADVERSARIAL=1 scripts/run-openrouter-sweep.sh   # safety sweep (adds -adv suffix)
#
# Provider: uses --provider openrouter (the capturing variant), which
# writes generation_id / upstream provider / authoritative
# usage.cost per call into journals-$tag/<ts>-<task>-n<NNN>-<kind>-calls.jsonl.

set -euo pipefail

ITERATIONS=${1:-3}
ONLY=${ONLY:-}

# One of: default (default), adversarial, prompt-injection, tool-confusion,
# identity-hijack. Picks the reflector system prompt and the per-model
# directory suffix (`-adv`, `-pi`, `-tc`, `-ih`) so each variant's data
# stays isolated from the default sweep.
VARIANT=${VARIANT:-}
ADVERSARIAL=${ADVERSARIAL:-0}
if [[ -n "$VARIANT" ]]; then
    case "$VARIANT" in
        default)          TAG_SUFFIX="";     EXTRA_FLAGS=();;
        adversarial)      TAG_SUFFIX="-adv"; EXTRA_FLAGS=("--adversarial-variant" "adversarial");;
        prompt-injection) TAG_SUFFIX="-pi";  EXTRA_FLAGS=("--adversarial-variant" "prompt-injection");;
        tool-confusion)   TAG_SUFFIX="-tc";  EXTRA_FLAGS=("--adversarial-variant" "tool-confusion");;
        identity-hijack)  TAG_SUFFIX="-ih";  EXTRA_FLAGS=("--adversarial-variant" "identity-hijack");;
        homoglyph)        TAG_SUFFIX="-hg";  EXTRA_FLAGS=("--adversarial-variant" "homoglyph");;
        multi-stage)      TAG_SUFFIX="-ms";  EXTRA_FLAGS=("--adversarial-variant" "multi-stage");;
        ciphered)         TAG_SUFFIX="-cf";  EXTRA_FLAGS=("--adversarial-variant" "ciphered");;
        non-english)      TAG_SUFFIX="-ne";  EXTRA_FLAGS=("--adversarial-variant" "non-english");;
        paraphrase)       TAG_SUFFIX="-pp";  EXTRA_FLAGS=("--adversarial-variant" "paraphrase");;
        html-comment-smuggle) TAG_SUFFIX="-hc"; EXTRA_FLAGS=("--adversarial-variant" "html-comment-smuggle");;
        markdown-fence)   TAG_SUFFIX="-mf";  EXTRA_FLAGS=("--adversarial-variant" "markdown-fence");;
        # v7 task-side variants. Selected with TASK_VARIANT= rather than
        # VARIANT= because they apply to the task-agent prompt, not the
        # reflector's. (--task-adversarial-variant on the binary side.)
        pr-title-injection) TAG_SUFFIX="-pti"; EXTRA_FLAGS=("--task-adversarial-variant" "pr-title-injection");;
        *) echo "unknown VARIANT='$VARIANT'" >&2; exit 2;;
    esac
elif [[ "$ADVERSARIAL" == "1" ]]; then
    TAG_SUFFIX="-adv"
    EXTRA_FLAGS=("--adversarial-reflector")
else
    TAG_SUFFIX=""
    EXTRA_FLAGS=()
fi

# Broadcast trace label shipped on every request so observability
# dashboards (Langfuse/Helicone/PostHog, wired through OpenRouter
# Settings → Observability) can pivot on default vs adversarial.
export OPENROUTER_TRACE_ENV="${OPENROUTER_TRACE_ENV:-v4${TAG_SUFFIX:--default}}"
export OPENROUTER_USER="${OPENROUTER_USER:-symbiont-karpathy-loop}"

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
    base_tag="${entry%%:*}"
    model="${entry#*:}"
    tag="${base_tag}${TAG_SUFFIX}"

    if [[ -n "$ONLY" && "$ONLY" != "$base_tag" && "$ONLY" != "$tag" ]]; then
        continue
    fi

    echo
    echo "============================================================"
    echo "  $tag — $model ${TAG_SUFFIX:+[adversarial]}"
    echo "============================================================"

    mkdir -p "data/$tag" "journals-$tag"
    rm -f "data/$tag/"*.db "journals-$tag/"*.json "journals-$tag/"*.jsonl
    : > "demo-output/$tag.log"

    # --provider openrouter uses our capturing client; it reads
    # OPENROUTER_API_KEY + OPENROUTER_MODEL from env.
    start=$(date +%s)
    OPENROUTER_MODEL="$model" \
    "$BIN" \
        --db "data/$tag/runs.db" \
        --journals-dir "journals-$tag" \
        --provider openrouter \
        demo --iterations "$ITERATIONS" "${EXTRA_FLAGS[@]}" \
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
