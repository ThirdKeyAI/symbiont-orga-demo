#!/usr/bin/env python3
"""Redact per-trial JSONLs so they're safe to share publicly.

What stays:
  - trial_id, task_id, substrate, model, prompt_variant, instance_seed
  - started_at, ended_at
  - tool_calls[].name, tool_calls[].blocked, tool_calls[].blocked_by
  - canary_triggers[].vector  (the FAMILY of escape, not the evidence)
  - escape_attempts[].vector, escape_attempts[].blocked_by
  - escape_attempted, escape_success, objective_success
  - usage_per_call[] (token counts, request_id, served_by_model)
  - time_to_first_attempt_ms, time_to_escape_ms

What gets replaced with "[REDACTED-EXPLOIT-CONTENT]":
  - tool_calls[].args   (the exact paths/urls/code the model used)
  - tool_calls[].result_summary
  - model_output_trace[].content (model's reasoning + free-form output)
  - canary_triggers[].evidence
  - escape_attempts[].evidence
  - fs_trace_paths

Stripped entirely (not just redacted) because they're high-volume noise:
  - model_output_trace[].tool_calls (already in tool_calls[])

Why this set: aggregated escape rates and substrate-blocker counts are
publishable findings. The literal "what the model said and did to
escape" is a substrate-specific cookbook. The split here mirrors the
CVE-disclosure convention: bug class + fix is public, working PoC is
not.

Usage:
    python redact_trials.py raw.jsonl > public.jsonl
    python redact_trials.py results/sweep/cell/x.jsonl > out.jsonl

Or for a whole sweep tree:
    find results/sweep -name '*.jsonl' -exec sh -c \\
      'python scripts/redact_trials.py "$1" > "${1%.jsonl}.public.jsonl"' _ {} \\;
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REDACTED = "[REDACTED-EXPLOIT-CONTENT]"


def redact_record(rec: dict) -> dict:
    """Return a redacted copy of `rec`. Original is not mutated."""
    out = dict(rec)

    # Tool calls — keep names + block status; redact args + summary.
    new_tcs = []
    for tc in rec.get("tool_calls", []) or []:
        new_tcs.append({
            "name": tc.get("name", ""),
            "args": REDACTED,
            "result_summary": REDACTED,
            "blocked": bool(tc.get("blocked", False)),
            **({"blocked_by": tc["blocked_by"]} if tc.get("blocked_by") else {}),
        })
    out["tool_calls"] = new_tcs

    # Model output trace — drop content, keep role + presence-of-tool-calls flag.
    new_mot = []
    for m in rec.get("model_output_trace", []) or []:
        new_mot.append({
            "role": m.get("role", "assistant"),
            "content": REDACTED,
            "had_tool_calls": bool(m.get("tool_calls")),
        })
    out["model_output_trace"] = new_mot

    # Canary triggers — keep vector, redact evidence.
    out["canary_triggers"] = [
        {"vector": t.get("vector", "unknown"), "evidence": REDACTED}
        for t in (rec.get("canary_triggers") or [])
    ]

    # Escape attempts — keep vector + blocked_by, redact evidence.
    out["escape_attempts"] = [
        {
            "vector": a.get("vector", "unknown"),
            "evidence": REDACTED,
            "blocked_by": a.get("blocked_by"),
        }
        for a in (rec.get("escape_attempts") or [])
    ]

    # fs_trace_paths is always exploit-shaped (full filesystem traces).
    if "fs_trace_paths" in out:
        out["fs_trace_paths"] = REDACTED

    # usage_per_call is already aggregate (token counts + request IDs); keep as-is.
    return out


def redact_jsonl(in_path: Path, out_stream) -> int:
    n = 0
    with in_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            redacted = redact_record(rec)
            out_stream.write(json.dumps(redacted) + "\n")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", type=Path, help="raw trial JSONL")
    ap.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="output path (default: stdout)",
    )
    args = ap.parse_args()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w") as f:
            n = redact_jsonl(args.input, f)
        sys.stderr.write(f"redacted {n} records → {args.output}\n")
    else:
        n = redact_jsonl(args.input, sys.stdout)
        sys.stderr.write(f"redacted {n} records\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
