"""Audit evidence supplements actual execution and cannot replace it."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('audit_fixture', SCRIPTS / 'verify_runtime_audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_plan_covers_restart_storage_failure_and_interruption():
    names = [case[0] for case in module.CASES]
    assert len(names) == len(set(names)) == 5
    assert set(names) == {'allowed_and_restart', 'unsafe_directory', 'symlink_directory', 'write_failure', 'deadline'}


def test_inert_success_cannot_prove_signed_allowed_effects():
    result = module.run_case(Path('/usr/bin/true'), ('allowed_and_restart',), 'unused')
    assert not result['valid'] and not result['passed']
    assert 'missing runtime audit reference' in result['evidence_error']


def test_additional_evidence_cannot_rescue_missing_execution():
    result = module.common.run_case(Path('/usr/bin/true'), module.common.CASES[1],
                                   evidence_verifier=lambda *_: {'passed': True})
    assert result['extra_evidence']['passed']
    assert not result['valid'] and not result['passed']


def test_inert_failure_cannot_prove_required_storage_failure():
    result = module.run_case(Path('/usr/bin/false'), ('unsafe_directory',), 'unused')
    assert not result['valid'] and not result['passed']
