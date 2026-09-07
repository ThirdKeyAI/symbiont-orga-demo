"""A completed process or an absent effect cannot stand in for a live MCP run."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("verify_runtime_mcp", SCRIPTS / "verify_runtime_mcp.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_mcp_plan_has_unique_positive_and_adversarial_cases():
    assert len({case[0] for case in module.CASES}) == len(module.CASES)
    assert {case[-1] for case in module.CASES} >= {"success", "policy", "verification", "pin", "stream", "deadline", "backend"}


def test_inert_process_does_not_pass_mcp_denial_case(monkeypatch):
    monkeypatch.setattr(module, "signed_schema", lambda: ({}, "synthetic public key"))
    monkeypatch.setattr(module, "worker_cleanup", lambda *args: ([], None))
    record = module.run_case(Path("/usr/bin/true"), module.CASES[1])
    assert record["exit_code"] == 0
    assert record["observed_files"] == []
    assert record["valid"] is False
    assert record["passed"] is False


def test_key_store_fault_preserves_independent_supervisor_state(monkeypatch):
    import subprocess
    import tomllib
    seen = []
    def execute(binary, root, *args, **kwargs):
        config = tomllib.loads((root / "symbiont.toml").read_text())
        state = Path(config["sandbox"]["docker"]["supervisor"]["state_dir"])
        assert not state.is_relative_to(root / "home/.symbiont")
        assert (root / "home/.symbiont").is_file()
        seen.append(True)
        return subprocess.CompletedProcess([], 0, "", ""), [], []
    monkeypatch.setattr(module.common, "execute_fixture", execute)
    monkeypatch.setattr(module, "signed_schema", lambda: ({}, "synthetic public key"))
    monkeypatch.setattr(module, "worker_cleanup", lambda *args: ([], None))
    case = next(case for case in module.CASES if case[0] == "mcp_key_store_failure")
    module.run_case(Path("/unused"), case)
    assert seen == [True]
