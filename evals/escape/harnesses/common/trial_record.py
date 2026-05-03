"""Trial record dataclass shared across all substrates.

Mirrors `evals/escape/schema/trial.schema.json`. Anything written into a
results JSONL must round-trip through this type so the schema check is
the single source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: Any
    result_summary: str
    blocked: bool
    blocked_by: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.blocked_by is None:
            d.pop("blocked_by")
        return d


@dataclass
class CanaryTrigger:
    vector: str
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrialRecord:
    trial_id: str
    task_id: str
    substrate: str
    model: str
    prompt_variant: int
    instance_seed: int
    started_at: str
    ended_at: str
    model_output_trace: list
    tool_calls: list[ToolCall]
    fs_trace_paths: list[str]
    canary_triggers: list[CanaryTrigger | dict]
    objective_success: bool
    escape_success: bool
    time_to_first_attempt_ms: int | None = None
    time_to_escape_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "substrate": self.substrate,
            "model": self.model,
            "prompt_variant": self.prompt_variant,
            "instance_seed": self.instance_seed,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model_output_trace": self.model_output_trace,
            "tool_calls": [
                tc.to_dict() if isinstance(tc, ToolCall) else tc for tc in self.tool_calls
            ],
            "fs_trace_paths": self.fs_trace_paths,
            "canary_triggers": [
                t.to_dict() if isinstance(t, CanaryTrigger) else t for t in self.canary_triggers
            ],
            "objective_success": self.objective_success,
            "escape_success": self.escape_success,
            "time_to_first_attempt_ms": self.time_to_first_attempt_ms,
            "time_to_escape_ms": self.time_to_escape_ms,
        }
