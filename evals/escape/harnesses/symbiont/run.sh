#!/usr/bin/env bash
# Wrapper that invokes the symbi-escape-bench binary with the same arg
# shape as `python -m harnesses.python_baseline`. The runner doesn't
# need to know which substrate it's calling — both expose the same
# CLI contract.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
BIN="$REPO_ROOT/target/release/symbi-escape-bench"
if [ ! -x "$BIN" ]; then
    BIN="$REPO_ROOT/target/debug/symbi-escape-bench"
fi
exec "$BIN" "$@"
