"""Agent selection failures need precise preflight evidence, not an inert process."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("selection_fixture", SCRIPTS / "verify_runtime_agent_selection.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_selection_plan_has_positives_and_exact_rejections():
    assert len(module.CASES) == len({case[0] for case in module.CASES}) == 11
    assert sum(case[3] is not None for case in module.CASES) == 7
    assert sum(case[3] is None and not case[4] for case in module.CASES) == 3


def test_inert_failure_does_not_prove_preflight_rejection():
    case = next(case for case in module.CASES if case[0] == "selected_unavailable")
    record = module.run_case(Path("/usr/bin/false"), case, "unused")
    assert record["exit_code"] == 1
    assert record["request_count"] == 0
    assert not record["valid"] and not record["passed"]
