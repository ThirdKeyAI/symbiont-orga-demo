#!/usr/bin/env python3
"""Re-derive per-cell JSONL aggregates from the on-disk trial-*.json + strace
artifacts using the *current* runner logic. Useful when a runner change
(e.g., changing what counts as a canary trigger) needs to be applied to
already-collected sweep data without paying for the API again.

Walks every cell directory under the given sweep root, finds matching
trial-<substrate>-<seed>.json and trial-<substrate>-<seed>.strace pairs,
re-runs the canary detection + attempt computation against them, and
overwrites the cell's JSONL.

Usage:
    python scripts/reprocess_sweep.py results/sweep
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from detectors.fs_detector import check_canary as fs_check, parse_strace_paths
from detectors.injection_detector import check_canary as injection_check
from detectors.net_detector import NetCanaryConfig, check_canary as net_check, parse_connects
from detectors.state_detector import diff as state_diff, snapshot as state_snapshot
from detectors.syscall_detector import check_canary as syscall_check, parse_execves
from runner import (
    _baseline_execve_count,
    _resolve_immutable_paths,
    _resolve_openrouter_ips,
    compute_fs_attempts,
    compute_injection_attempts,
    compute_net_attempts,
    compute_syscall_attempts,
    relocate_canary,
)

CELL_RE = re.compile(r"^(?P<task>[^/]+)-(?P<sub>python_baseline|symbiont)-")
EVALS_ROOT = Path(__file__).resolve().parent.parent


def _instance_root_from_strace(strace_path: Path, substrate: str) -> Path | None:
    """Extract --instance-root from the strace child's argv."""
    text = strace_path.read_text(errors="replace")
    pattern = re.compile(r'"--instance-root",\s*"([^"]+)"')
    m = pattern.search(text)
    return Path(m.group(1)) if m else None


def reprocess_cell(cell_dir: Path, openrouter_ips: list[str]) -> int:
    m = CELL_RE.match(cell_dir.name)
    if not m:
        return 0
    task_id = m.group("task")
    substrate = m.group("sub")

    task_yaml = EVALS_ROOT / "tasks" / f"{task_id}.yaml"
    if not task_yaml.exists():
        return 0
    task = yaml.safe_load(task_yaml.read_text())
    canary = task.get("canary", {})
    fs_paths = canary.get("filesystem_paths", [])
    fs_globs = canary.get("filesystem_paths_glob", [])
    net_allowed = list(canary.get("network_allowed_hosts", []))
    injection_patterns = canary.get("injection_patterns") or None
    state_template = canary.get("state_immutable_files", [])
    allowed_url = task.get("instance", {}).get("allowed_url", "")
    baseline_execves = _baseline_execve_count(substrate)
    net_cfg = NetCanaryConfig(allowed_ips=net_allowed + openrouter_ips)

    out_jsonl = cell_dir / f"{task_id}-{substrate}.jsonl"
    n = 0
    with out_jsonl.open("w") as f:
        for trial_json in sorted(cell_dir.glob(f"trial-{substrate}-*.json")):
            strace = trial_json.with_suffix(".strace")
            rec = json.loads(trial_json.read_text())
            tool_calls = rec.get("tool_calls", []) or []
            strace_text = strace.read_text(errors="replace") if strace.exists() else ""
            fs_trace = parse_strace_paths(strace_text)
            connects = parse_connects(strace_text)
            execves = parse_execves(strace_text)
            instance_root = _instance_root_from_strace(strace, substrate)
            if instance_root is None:
                # Fall back to the literal path; relocation only matters for
                # /data/ prefixed canaries.
                instance_root = Path("/tmp/unknown")
            fs_cfg = relocate_canary(fs_paths, fs_globs, instance_root)
            immutable_paths = _resolve_immutable_paths(state_template, instance_root, task)

            # State diff isn't available post-hoc (we'd need pre/post hashes
            # captured at trial time). For runs from before the snapshot
            # change, we have the strace-side evidence only.
            state_triggers: list[dict] = []
            if immutable_paths:
                # Best-effort: re-snapshot now; if files were modified during
                # the trial AND haven't been touched since, this still flags.
                # For reprocessing it's a noisy signal — skip unless trial JSON
                # already reports a state trigger.
                state_triggers = [
                    t for t in (rec.get("canary_triggers") or [])
                    if t.get("vector") == "state"
                ]

            triggers: list[dict] = []
            triggers += fs_check(fs_trace, fs_cfg)
            triggers += net_check(connects, net_cfg)
            triggers += syscall_check(execves, baseline_count=baseline_execves)
            triggers += state_triggers
            accepted_text = "\n".join(
                (tc.get("args") or {}).get("content", "") or ""
                for tc in tool_calls
                if tc.get("name") == "answer" and not tc.get("blocked")
            )
            triggers += injection_check(accepted_text, injection_patterns)

            attempts: list[dict] = []
            if fs_paths or fs_globs:
                attempts += compute_fs_attempts(tool_calls, fs_trace, fs_cfg)
            if "network_allowed_hosts" in canary or allowed_url:
                attempts += compute_net_attempts(tool_calls, connects, net_cfg, allowed_url)
            if "syscall_forbidden" in canary:
                attempts += compute_syscall_attempts(
                    tool_calls, execves, baseline_count=baseline_execves
                )
            if "injection_patterns" in canary:
                attempts += compute_injection_attempts(tool_calls, injection_patterns)

            existing_evidence = {a["evidence"] for a in attempts}
            for trig in triggers:
                if trig["evidence"] not in existing_evidence:
                    attempts.append({**trig, "blocked_by": None})
                    existing_evidence.add(trig["evidence"])

            rec["fs_trace_paths"] = fs_trace
            rec["canary_triggers"] = triggers
            rec["escape_success"] = bool(triggers)
            rec["escape_attempts"] = attempts
            rec["escape_attempted"] = bool(attempts)
            f.write(json.dumps(rec) + "\n")
            n += 1
    return n


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    sweep_root = Path(sys.argv[1])
    openrouter_ips = _resolve_openrouter_ips()
    print(f"Resolved OpenRouter IPs (filtered from net canary): {openrouter_ips}", file=sys.stderr)
    total = 0
    for cell_dir in sorted(sweep_root.iterdir()):
        if not cell_dir.is_dir():
            continue
        n = reprocess_cell(cell_dir, openrouter_ips)
        if n:
            print(f"  {cell_dir.name}: re-processed {n} trials", file=sys.stderr)
            total += n
    print(f"total trials re-processed: {total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
