"""No inert process establishes scheduled execution or policy enforcement."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("scheduler_fixture", SCRIPTS / "verify_runtime_scheduler.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_plan_covers_real_payloads_lifecycle_and_preflight_failures():
    names = [case[0] for case in module.CASES]
    assert len(names) == len(set(names)) == 7
    assert set(names) == {"manual_payload", "timer_payload", "mandatory_approval", "policy_denial",
                          "missing_provider", "unavailable_selected", "cancel_active"}


def test_inert_success_cannot_prove_scheduled_effects():
    result = module.run_case(Path("/usr/bin/true"), ("manual_payload",), "unused")
    assert not result["valid"] and not result["passed"]
    assert "runtime exited during startup" in result["error"]
    assert result["inference_requests"] == 0


def test_inert_failure_cannot_prove_policy_enforcement():
    result = module.run_case(Path("/usr/bin/false"), ("mandatory_approval",), "unused")
    assert not result["valid"] and not result["passed"]
    assert "runtime exited during startup" in result["error"]
    assert result["inference_requests"] == 0
