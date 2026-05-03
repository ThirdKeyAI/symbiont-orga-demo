"""Harness wiring test — verifies the loop shape without hitting the LLM API.

Monkeypatches OpenRouterClient.chat to return a scripted sequence: first
turn calls read_csv, second turn calls answer with the right substring.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from harnesses.common.llm_client import LlmResponse, LlmUsage


def _zero_usage():
    return LlmUsage(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        request_id=None,
        served_by_model=None,
    )
from harnesses.python_baseline.harness import run_trial

TASK_PATH = Path(__file__).parent.parent / "tasks" / "fs_boundary.yaml"


def _scripted_responses(allowed_path: str):
    seq = iter(
        [
            LlmResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "read_csv",
                            "arguments": f'{{"path": "{allowed_path}"}}',
                        },
                    }
                ],
                raw={},
                usage=_zero_usage(),
            ),
            LlmResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {
                            "name": "answer",
                            "arguments": '{"content": "rows=3 sum=425"}',
                        },
                    }
                ],
                raw={},
                usage=_zero_usage(),
            ),
        ]
    )

    def fake_chat(self, messages, tools):
        return next(seq)

    return fake_chat


def test_run_trial_records_tool_calls_and_objective_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    task = yaml.safe_load(TASK_PATH.read_text())
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        allowed = str(root / task["instance"]["allowed_paths"][0].lstrip("/"))
        with patch(
            "harnesses.python_baseline.harness.OpenRouterClient.chat",
            _scripted_responses(allowed),
        ):
            rec = run_trial(
                task,
                model="test/model",
                prompt_variant=0,
                instance_seed=0,
                instance_root=root,
                max_turns=4,
            )
    assert [tc.name for tc in rec.tool_calls] == ["read_csv", "answer"]
    assert rec.objective_success is True
    assert rec.substrate == "python_baseline"
