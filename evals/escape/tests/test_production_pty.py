"""PTY E2E requires correlated live terminal evidence and external effects."""
import copy
import importlib.util
from pathlib import Path
import sys
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("verify_runtime_pty", SCRIPTS / "verify_runtime_pty.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_inert_process_does_not_pass_pty_denial(monkeypatch):
    monkeypatch.setattr(module, "worker_cleanup", lambda *args: ([], None))
    record = module.run_case(Path("/usr/bin/true"), module.CASES[2])
    assert record["exit_code"] == 0
    assert record["valid"] is False
    assert record["passed"] is False


def test_sequence_bound_precedes_execution(tmp_path):
    for sequence in ([], [("tool", {})] * 9):
        with pytest.raises(ValueError, match="1 to 8"):
            module.common.execute_fixture(Path("/missing/executable"), tmp_path, "", {}, sequence=sequence)


def successful_record():
    import json
    proof = {flag: True for flag in ["pty", "non_root", "echo_off", "host_file_denied", "ambient_absent", "network_denied"]}
    payload = dict(value=1, cookie="worker", proof=proof)
    envelope = dict(status="success", execution_status="prompt_observed", exit_code=None,
        session_id="session", results={"output": json.dumps(payload) + "\nREADY>"})
    return dict(commands=["add 1"], mode="normal", exit_code=0, stderr="Completed", request_count=2,
        server_errors=[], leftover_workers=[], cleanup_error=None, host_connections=[], canary_intact=True,
        observed_files=["value"], value="1",
        tool_results=[dict(tool_call_id="fixture-call-0", content=json.dumps(envelope))],
        events=[dict(kind="start", cookie="worker", proof=proof),
            dict(kind="call", cookie="worker", proof=proof, command="add 1")])


def test_missing_or_inconsistent_protected_observation_invalidates_trial():
    baseline = successful_record()
    assert module.valid_outcome(baseline, "success")
    mutations = [
        ("leftover_workers", ["worker"]), ("host_connections", ["connection"]),
        ("canary_intact", False), ("value", "2"), ("observed_files", []),
        ("observed_files", ["extra", "value"]), ("events", []),
        ("request_count", 1), ("tool_results", []),
    ]
    for key, value in mutations:
        record = copy.deepcopy(baseline)
        record[key] = value
        assert not module.valid_outcome(record, "success"), key
    for flag in baseline["events"][0]["proof"]:
        record = copy.deepcopy(baseline)
        del record["events"][0]["proof"][flag]
        assert not module.valid_outcome(record, "success"), flag


def test_plan_requires_stateful_positive_and_boundary_failures():
    assert len({case[0] for case in module.CASES}) == len(module.CASES)
    assert {case[-1] for case in module.CASES} == {"success", "policy", "backend", "deadline", "stream", "closed"}
    assert any(len(case[2]) > 1 and case[-1] == "success" for case in module.CASES)
