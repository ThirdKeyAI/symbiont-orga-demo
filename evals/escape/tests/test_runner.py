"""Runner shape test — runs 3 trials of a stub substrate, asserts JSONL shape."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from runner import run_pilot


def _stub_run_one(
    task,
    *,
    substrate,
    model,
    prompt_variant,
    instance_seed,
    results_dir,
    instance_root,
):
    from harnesses.common.trial_record import TrialRecord

    rec = TrialRecord(
        trial_id=f"stub-{instance_seed}",
        task_id=task["id"],
        substrate=substrate,
        model=model,
        prompt_variant=prompt_variant,
        instance_seed=instance_seed,
        started_at="2026-05-02T00:00:00Z",
        ended_at="2026-05-02T00:00:01Z",
        model_output_trace=[],
        tool_calls=[],
        fs_trace_paths=[],
        canary_triggers=[],
        objective_success=True,
        escape_success=False,
    )
    out = results_dir / f"trial-{substrate}-{instance_seed:04d}.json"
    out.write_text(json.dumps(rec.to_dict()))
    strace = results_dir / f"trial-{substrate}-{instance_seed:04d}.strace"
    strace.write_text("")
    return out, strace


def test_run_pilot_emits_jsonl_with_n_records(tmp_path):
    task_path = Path(__file__).parent.parent / "tasks" / "fs_boundary.yaml"
    with patch("runner._run_one_trial", _stub_run_one):
        out = run_pilot(
            task_path=task_path,
            substrate="python_baseline",
            model="test/model",
            trials=3,
            results_dir=tmp_path,
        )
    lines = out.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        rec = json.loads(line)
        assert rec["task_id"] == "fs_boundary"
        assert rec["substrate"] == "python_baseline"
