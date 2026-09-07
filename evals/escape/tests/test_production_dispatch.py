"""The production driver must not count an inert successful process as E2E."""
from pathlib import Path
import importlib.util


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_runtime_dispatch.py"
spec = importlib.util.spec_from_file_location("verify_runtime_dispatch", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_process_exit_zero_without_real_exchange_is_invalid():
    result = module.run_case(Path("/usr/bin/true"), module.CASES[1])
    assert result["exit_code"] == 0
    assert result["observed_files"] == result["expected_files"] == []
    assert result["valid"] is False
    assert result["passed"] is False


def test_plan_has_unique_positive_and_negative_cases():
    assert len({case[0] for case in module.CASES}) == len(module.CASES)
    assert any(case[-1] for case in module.CASES)
    assert any(not case[-1] for case in module.CASES)


def test_default_main_preserves_plan_when_container_backend_is_missing(monkeypatch, tmp_path):
    import json
    import sys
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["fixture", "--source", str(tmp_path), "--target-dir", str(tmp_path / "target"), "--report", str(report)])
    def missing(*args, **kwargs):
        raise FileNotFoundError("synthetic missing docker")
    monkeypatch.setattr(module.subprocess, "check_output", missing)
    assert module.main() == 1
    evidence = json.loads(report.read_text())
    assert evidence["planned_cases"] == [case[0] for case in module.CASES]
    assert evidence["status"] == "invalid"
    assert evidence["trials"] == []
