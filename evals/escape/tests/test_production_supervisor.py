"""Lifecycle evidence requires a real trigger, correlated result and external cleanup."""
import copy
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("verify_runtime_supervisor", SCRIPTS / "verify_runtime_supervisor.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def active_record():
    return dict(case="runtime_sigkill_active", exit_code=-9, request_count=1, killed_after_trigger=True,
        ticks_stopped=True, observed_files=["proof", "ticks"], canary_intact=True,
        cleanup_error=None, leftover_workers=[], leftover_leases=[], server_errors=[],
        proof=dict(non_root=True, host_file_denied=True, supervisor_denied=True, ambient_absent=True))


def test_missing_trigger_effect_or_cleanup_invalidates_crash_trial():
    baseline = active_record()
    assert module.valid_outcome(baseline)
    for key, value in [("exit_code", 0), ("request_count", 0), ("killed_after_trigger", False),
            ("ticks_stopped", False), ("observed_files", []), ("canary_intact", False),
            ("leftover_workers", ["owned"]), ("leftover_leases", ["unresolved"]), ("proof", {})]:
        changed = copy.deepcopy(baseline)
        changed[key] = value
        assert not module.valid_outcome(changed), key


def test_empty_container_inventory_does_not_prove_late_creation_test():
    record = dict(active_record(), case="runtime_sigkill_creating", observed_files=[], late_container_created=False)
    assert not module.valid_outcome(record)
    record["late_container_created"] = True
    assert module.valid_outcome(record)


def test_inert_process_does_not_pass_unavailable_supervisor(monkeypatch):
    monkeypatch.setattr(module, "workers", lambda *_: [])
    record = module.run_case(Path("/usr/bin/true"), ("supervisor_unavailable",))
    assert record["passed"] is False


def test_crash_observer_rejects_process_without_live_effect(monkeypatch):
    import pytest
    monkeypatch.setattr(module, "workers", lambda *_: [])
    with pytest.raises(AssertionError, match="before the expected live worker"):
        module.run_case(Path("/usr/bin/true"), ("runtime_sigkill_active",))


def test_failed_trial_keeps_unresolved_durable_recovery_state():
    import shutil
    with module.fixture_directory() as root:
        (root / "leases").mkdir()
        (root / "leases/unresolved.json").write_text("synthetic retained state")
    try:
        assert (root / "leases/unresolved.json").exists()
    finally:
        shutil.rmtree(root)


def test_success_text_cannot_stand_in_for_unavailable_supervisor_error():
    record = dict(active_record(), case="supervisor_unavailable", exit_code=0, request_count=2,
        observed_files=[], stderr="Completed", tool_results=[dict(tool_call_id="fixture-call", content='{"status":"success","supervisor":"ready"}')])
    assert not module.valid_outcome(record)
    record["tool_results"][0]["content"] = "[Error] ToolClad error: Tool 'owner_fixture': sandbox supervisor executable unavailable"
    assert module.valid_outcome(record)

def test_protected_state_mount_requires_preflight_rejection_without_inference():
    record = dict(active_record(), case="supervisor_state_mount_denied", exit_code=1,
        request_count=0, tool_results=[], observed_files=[],
        stderr="Agent sandbox selection failed: bind mount exposes the protected sandbox supervisor state")
    assert module.valid_outcome(record)
    for key, value in [("exit_code",0), ("request_count",1),
            ("stderr","unrelated configuration failure"), ("tool_results",[{}]), ("observed_files",["effect"])]:
        changed = dict(record); changed[key] = value
        assert not module.valid_outcome(changed), key
