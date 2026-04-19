#!/usr/bin/env bash
# Drives the full demo end-to-end. Idempotent: wipes the run database first
# so you get a clean curve every time.
#
# Usage:
#   scripts/run-demo.sh                  # 3 iterations × 3 tasks, mock provider
#   scripts/run-demo.sh 10               # 10 iterations × 3 tasks, mock provider
#   PROVIDER=cloud scripts/run-demo.sh 5 # real cloud LLM, 5 iterations
#
# Environment:
#   PROVIDER   'mock' (default) or 'cloud'
#   DB         Path to runs.db (default data/runs.db)
#   TASKS_DIR  Tasks directory (default tasks)
#   POLICIES   Policies directory (default policies)

set -euo pipefail

ITERATIONS=${1:-3}
PROVIDER=${PROVIDER:-mock}
DB=${DB:-data/runs.db}
TASKS_DIR=${TASKS_DIR:-tasks}
POLICIES=${POLICIES:-policies}

# Source a project-local .env so operators can pipe their LLM API key
# through a single file instead of exporting it in every shell. The
# file is gitignored.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . .env
    set +a
fi

# Build once to amortise compile cost and silence per-invocation progress
# output. `--message-format=short` keeps the upstream symbi-runtime
# deprecation/let-else warnings from swamping the dashboard.
echo "→ building symbi-kloop-bench (release)"
cargo build --release --quiet -p symbi-kloop-bench --message-format=short 2>&1 |
    grep -v "^warning:" || true
BIN=${BIN:-target/release/symbi-kloop-bench}

# Wipe previous demo data. Knowledge store lives under data/ too so the
# loop starts cold on every run.
rm -rf data/*.db journals demo-output/run-latest.md 2>/dev/null || true
mkdir -p data journals demo-output

echo "→ running $ITERATIONS iteration(s) across all tasks with --provider $PROVIDER"
"$BIN" --db "$DB" --tasks-dir "$TASKS_DIR" --policies-dir "$POLICIES" \
     --provider "$PROVIDER" demo --iterations "$ITERATIONS"

echo
echo "→ rendering dashboard"
"$BIN" --db "$DB" dashboard --limit 40

echo
echo "→ writing proof artifact"
"$BIN" --db "$DB" report --out "demo-output/run-$(date +%Y-%m-%d).md"

echo "done."
