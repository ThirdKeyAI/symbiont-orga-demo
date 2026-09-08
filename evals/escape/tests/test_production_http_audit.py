"""The HTTP shipping driver requires actual responses, effects and signed audit."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))
spec = importlib.util.spec_from_file_location('http_audit_fixture',SCRIPTS/'verify_runtime_http_audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_plan_covers_positive_storage_append_and_provider_failure():
    names = [case[0] for case in module.CASES]
    assert len(names) == len(set(names)) == 4
    assert set(names) == {'http_payload','http_audit_storage','http_audit_write_failure','http_provider_error'}


def test_inert_process_cannot_prove_http_audit():
    for binary,case in [('/usr/bin/true','http_payload'),('/usr/bin/false','http_audit_storage')]:
        result = module.run_case(Path(binary),(case,),'unused')
        assert not result['valid'] and not result['passed']
        assert 'runtime exited during startup' in result['error']
        assert result['inference_requests'] == 0
