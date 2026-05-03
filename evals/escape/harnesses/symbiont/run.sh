#!/usr/bin/env bash
# Wrapper that invokes the symbi-escape-bench binary with the same arg
# shape as `python -m harnesses.python_baseline`. The runner doesn't
# need to know which substrate it's calling — both expose the same
# CLI contract.
#
# IMPORTANT: this script must avoid spawning subprocesses (dirname,
# basename, etc.) because the syscall canary detector counts execve()
# calls in the strace trace; each subprocess shows up as an additional
# execve and the syscall_boundary task uses execve count > 1 as the
# canary signal. Using parameter expansion keeps the syscall count to
# just (bash, symbi-escape-bench) — exactly two execves the runner
# expects in the syscall task's baseline_count.
set -euo pipefail
script="${BASH_SOURCE[0]}"
# evals/escape/harnesses/symbiont/run.sh -> repo root is 4 levels up
d="${script%/*}"     # .../symbiont
d="${d%/*}"          # .../harnesses
d="${d%/*}"          # .../escape
d="${d%/*}"          # .../evals
repo_root="${d%/*}"  # repo root
bin="$repo_root/target/release/symbi-escape-bench"
if [ ! -x "$bin" ]; then
    bin="$repo_root/target/debug/symbi-escape-bench"
fi
exec "$bin" "$@"
