import json
from pathlib import Path

import jsonschema

from harnesses.common.trial_record import ToolCall, TrialRecord

SCHEMA = json.loads(
    (Path(__file__).parent.parent / "schema" / "trial.schema.json").read_text()
)


def test_record_serializes_to_schema_compliant_json():
    rec = TrialRecord(
        trial_id="t-001",
        task_id="fs_boundary",
        substrate="python_baseline",
        model="deepseek/deepseek-v4-pro",
        prompt_variant=0,
        instance_seed=42,
        started_at="2026-05-02T00:00:00Z",
        ended_at="2026-05-02T00:00:05Z",
        model_output_trace=[],
        tool_calls=[
            ToolCall(
                name="read_csv",
                args={"path": "/data/allowed/orders.csv"},
                result_summary="3 rows",
                blocked=False,
                blocked_by=None,
            )
        ],
        fs_trace_paths=["/data/allowed/orders.csv"],
        canary_triggers=[],
        objective_success=True,
        escape_success=False,
        escape_attempted=False,
        escape_attempts=[],
        time_to_first_attempt_ms=None,
        time_to_escape_ms=None,
    )
    payload = rec.to_dict()
    jsonschema.validate(payload, SCHEMA)
