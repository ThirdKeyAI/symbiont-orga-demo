import json

from analysis.aggregate import aggregate_jsonl, wilson_ci


def test_wilson_ci_known_values():
    lo, hi = wilson_ci(5, 10, 0.95)
    assert 0.23 < lo < 0.25
    assert 0.75 < hi < 0.77


def test_wilson_ci_zero_successes_lower_bound_is_zero():
    lo, hi = wilson_ci(0, 20, 0.95)
    assert lo == 0.0
    assert hi > 0


def test_aggregate_jsonl_groups_by_substrate(tmp_path):
    p = tmp_path / "x.jsonl"
    rows = [
        {
            "substrate": "a",
            "task_id": "t",
            "objective_success": True,
            "escape_success": False,
            "canary_triggers": [],
        },
        {
            "substrate": "a",
            "task_id": "t",
            "objective_success": True,
            "escape_success": True,
            "canary_triggers": [{"vector": "filesystem", "evidence": "/etc/passwd"}],
        },
        {
            "substrate": "b",
            "task_id": "t",
            "objective_success": False,
            "escape_success": False,
            "canary_triggers": [],
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    summary = aggregate_jsonl([p])
    assert summary[("t", "a")]["n"] == 2
    assert summary[("t", "a")]["escapes"] == 1
    assert summary[("t", "b")]["n"] == 1
    assert summary[("t", "a")]["vector_breakdown"]["filesystem"] == 1
